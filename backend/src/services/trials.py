import hashlib
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from src.models.entities import Cohort, Event, Intervention, Trial

TZ = ZoneInfo('Asia/Shanghai')


def monday(day):
    return day - timedelta(days=day.weekday())


def assignment_basis(cohort_id, cohort_week, week_index):
    """固定分组依据：同一冻结方案/首学周/成熟周单元格，分组永不随重试改变。"""
    return f'cohort={cohort_id}|week={cohort_week}|idx={week_index}'


def build_assignment(members, basis):
    """按首学周（同一单元格）随机均分。哈希排序确定，奇数差一人，试验组多一人。

    逐人保留哈希与序号，页面可核对每位学员的分组依据。
    """
    ranked = []
    for learner in members:
        digest = hashlib.sha256(f'{basis}|learner={learner}'.encode()).hexdigest()
        ranked.append({'learner': learner, 'hash': digest})
    ranked.sort(key=lambda x: (x['hash'], x['learner']))
    treatment_size = (len(ranked) + 1) // 2  # 奇数时试验组多一人
    treatment, control = [], []
    for rank, item in enumerate(ranked):
        group = 'treatment' if rank < treatment_size else 'control'
        entry = {'learner': item['learner'], 'group': group, 'rank': rank,
                 'hash': item['hash'], 'basis': basis}
        (treatment if group == 'treatment' else control).append(entry)
    return {
        'basis': basis,
        'method': 'sha256(分组依据|学员编号) 升序排序后均分；奇数名单试验组多一人；失败重试不改换分组。',
        'source_members': sorted(members),
        'treatment': treatment,
        'control': control,
    }


def get_cell(cohort, cohort_week, week_index):
    if cohort.state != 'done':
        raise HTTPException(409, '队列计算完成后才能建立对照试验')
    row = next((r for r in cohort.result['rows'] if r['cohort_week'] == cohort_week), None)
    if not row:
        raise HTTPException(404, '所选首学队列不在该冻结方案中')
    cell = next((c for c in row['cells'] if c['week_index'] == week_index), None)
    if not cell or not cell['mature']:
        raise HTTPException(409, '只能从成熟周缺席成员建立对照试验')
    if not cell['missing']:
        raise HTTPException(409, '该成熟周全部回访，没有可分组的缺席成员')
    return row, cell


def observation_window(publish_week, observe_weeks):
    """观察期 = 发布周起的 observe_weeks 个完整周，返回各周周一与期末周日。"""
    weeks = [publish_week + timedelta(weeks=k) for k in range(1, observe_weeks + 1)]
    end = weeks[-1] + timedelta(days=6)
    return weeks, end


def overlaps(a_start, a_end, b_start, b_end):
    return not (a_end < b_start or b_end < a_start)


def active_trials(s):
    """观察中或已结算的试验，其观察期持续占用学员互斥资格。"""
    return s.query(Trial).filter(Trial.state.in_(['observing', 'settled'])).all()


def learner_conflicts(s, learners, ignore_id=None):
    """返回 {学员: [冲突试验摘要]}；同一学员观察期重叠的试验互斥。"""
    hit = {}
    for t in active_trials(s):
        if ignore_id is not None and t.id == ignore_id:
            continue
        r = t.result or {}
        start = date.fromisoformat(r['publish_week'])
        end = date.fromisoformat(r['observation_end'])
        members = {e['learner'] for e in t.assignment['treatment'] + t.assignment['control']}
        for learner in learners:
            if learner in members:
                hit.setdefault(learner, []).append(
                    {'trial_id': t.id, 'trial_name': t.name,
                     'window_start': start.isoformat(), 'window_end': end.isoformat()})
    return hit


def approved_members(s, learners):
    """已签核辅导清单覆盖的学员，发布时必须排除。"""
    covered = set()
    rows = s.query(Intervention).filter(Intervention.status == 'approved').all()
    for iv in rows:
        covered.update(iv.members or [])
    return sorted(covered.intersection(learners))


def assert_control_guard(s, members):
    """对照组观察期禁止签核辅导：命中任一观察中试验对照组即拦截。"""
    member_set = set(members)
    for t in s.query(Trial).filter(Trial.state == 'observing').all():
        control = {e['learner'] for e in t.assignment['control']}
        blocked = sorted(member_set & control)
        if blocked:
            raise HTTPException(
                409, '学员处于对照试验《%s》对照组观察期，禁止签核辅导：%s'
                % (t.name, '、'.join(blocked)))


def create_trial(s, cohort, data):
    row, cell = get_cell(cohort, data.cohort_week, data.week_index)
    basis = assignment_basis(cohort.id, data.cohort_week, data.week_index)
    assignment = build_assignment(sorted(cell['missing']), basis)
    trial = Trial(
        name=data.name, cohort_id=cohort.id, cohort_week=data.cohort_week,
        week_index=data.week_index, observe_weeks=data.observe_weeks,
        state='pending', assignment=assignment,
    )
    s.add(trial)
    s.flush()  # 取得 id
    members = assignment['source_members']
    # 创建时即提示冲突与已签核成员，名单与分组依据同时冻结；最终拦截在发布时执行。
    trial.result = {
        'preview': {
            'conflicts': learner_conflicts(s, members, ignore_id=trial.id),
            'approved_members': approved_members(s, members),
        }
    }
    s.commit()
    return trial


def publish_trial(s, trial):
    """待发布 -> 观察中。事务内加锁重算隔离，失败重试不改换分组。"""
    if trial.state == 'observing':
        return trial  # 幂等：重试直接返回，名单不动
    if trial.state != 'pending':
        raise HTTPException(409, '仅待发布试验可以发布')
    # 锁定全部试验与干预行，串行化并发发布/签核，守住观察期互斥。
    s.query(Trial).with_for_update().all()
    s.query(Intervention).with_for_update().all()
    members = trial.assignment['source_members']
    blocked_approved = approved_members(s, members)
    if blocked_approved:
        raise HTTPException(409, '名单含已签核辅导成员，禁止发布：' + '、'.join(blocked_approved))
    conflicts = learner_conflicts(s, members, ignore_id=trial.id)
    if conflicts:
        raise HTTPException(409, detail={
            'message': '学员观察期与在跑试验重叠，互斥拦截',
            'conflicts': conflicts,
        })
    today = datetime.now(TZ).date()
    publish_week = monday(today)
    weeks, end = observation_window(publish_week, trial.observe_weeks)
    # 试验组生成干预（草案，名单=冻结试验组）；对照组观察期禁止签核辅导。
    iv = Intervention(
        cohort_id=trial.cohort_id, cohort_week=trial.cohort_week,
        week_index=trial.week_index,
        note=f'对照试验《{trial.name}》试验组干预，发布周 {publish_week.isoformat()} 起 {trial.observe_weeks} 个完整周。',
        members=[e['learner'] for e in trial.assignment['treatment']],
        status='draft', trial_id=trial.id,
    )
    s.add(iv)
    s.flush()
    trial.intervention_id = iv.id
    trial.state = 'observing'
    trial.result = {
        'publish_week': publish_week.isoformat(),
        'published_at': datetime.now(TZ).isoformat(timespec='seconds'),
        'observation_weeks': [w.isoformat() for w in weeks],
        'observation_end': end.isoformat(),
        'excluded_approved': [],
        'assignment_frozen': trial.assignment['basis'],
    }
    s.commit()
    return trial


def _week_hits(s, learners, as_of):
    """每个学员在截至日前（上海业务日）回访过的 ISO 周一集合。"""
    hits = {x: set() for x in learners}
    future = 0
    for ev in s.query(Event).all():
        if ev.learner not in hits:
            continue
        ts = datetime.fromisoformat(ev.occurred_at.replace('Z', '+00:00'))
        if ts.tzinfo is None:
            raise HTTPException(500, '事件缺少时区，无法结算')
        day = ts.astimezone(TZ).date()
        if day > as_of:
            future += 1
            continue
        hits[ev.learner].add(monday(day))
    return hits, future


def _group_rate(group_entries, hits, weeks):
    """分母保留发布原名单；无事件算未回访。返回逐周与累计。"""
    members = [e['learner'] for e in group_entries]
    denominator = len(members)
    weekly = []
    for w in weeks:
        returned = [m for m in members if w in hits[m]]
        weekly.append({
            'target_week': w.isoformat(),
            'denominator': denominator,
            'returned': len(returned),
            'rate': (len(returned) / denominator) if denominator else None,
            'returned_members': sorted(returned),
            'not_returned_members': sorted(set(members) - set(returned)),
        })
    window_set = set(weeks)
    cum_returned = [m for m in members if hits[m] & window_set]
    cumulative = {
        'denominator': denominator,
        'returned': len(cum_returned),
        'rate': (len(cum_returned) / denominator) if denominator else None,
        'returned_members': sorted(cum_returned),
    }
    return {'members': members, 'weekly': weekly, 'cumulative': cumulative}


def settle_trial(s, trial, as_of):
    if trial.state == 'pending':
        raise HTTPException(409, '试验尚未发布，不能结算')
    if trial.state == 'settled':
        return trial  # 重复结算幂等，返回冻结结果，不重算
    r = trial.result
    end = date.fromisoformat(r['observation_end'])
    if end > as_of:
        raise HTTPException(409, f'观察期未成熟（期末周日 {r["observation_end"]} > 截至 {as_of}），暂不结算')
    weeks = [date.fromisoformat(w) for w in r['observation_weeks']]
    members = trial.assignment['source_members']
    hits, future = _week_hits(s, members, as_of)
    treatment = _group_rate(trial.assignment['treatment'], hits, weeks)
    control = _group_rate(trial.assignment['control'], hits, weeks)
    diffs = []
    for tw, cw in zip(treatment['weekly'], control['weekly']):
        diffs.append({
            'target_week': tw['target_week'],
            'treatment_rate': tw['rate'],
            'control_rate': cw['rate'],
            'diff': (tw['rate'] - cw['rate']) if tw['rate'] is not None and cw['rate'] is not None else None,
        })
    cumulative_diff = (treatment['cumulative']['rate'] - control['cumulative']['rate']) \
        if treatment['cumulative']['rate'] is not None and control['cumulative']['rate'] is not None else None
    trial.state = 'settled'
    trial.result = {
        **r,
        'settled_at': datetime.now(TZ).isoformat(timespec='seconds'),
        'as_of': as_of.isoformat(),
        'future_events_excluded': future,
        'treatment': treatment,
        'control': control,
        'weekly_diffs': diffs,
        'cumulative_diff': cumulative_diff,
        'method': ('按发布后完整周逐周比对两组回访率；分母保留发布时冻结原名单，'
                   '观察期内无任何事件的学员按未回访计；目标周周日≤截至日才纳入，'
                   '观察期未成熟不结算；重复结算返回首次冻结结果。'),
    }
    s.commit()
    return trial
