-- 对照试验（RCT）模块；承接 001_initial，新增 trials 表并在 interventions 回填试验关联。
CREATE TABLE `trials` (
  `id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(100) NOT NULL,
  `cohort_id` int DEFAULT NULL,
  `cohort_week` varchar(10) NOT NULL,
  `week_index` int NOT NULL,
  `observe_weeks` int NOT NULL,
  `state` varchar(20) DEFAULT 'pending',
  `assignment` json NOT NULL,
  `result` json DEFAULT NULL,
  `intervention_id` int DEFAULT NULL,
  `error` text,
  PRIMARY KEY (`id`),
  KEY `cohort_id` (`cohort_id`),
  CONSTRAINT `trials_ibfk_1` FOREIGN KEY (`cohort_id`) REFERENCES `cohorts` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

ALTER TABLE `interventions`
  ADD COLUMN `trial_id` int DEFAULT NULL AFTER `status`,
  ADD KEY `trial_id` (`trial_id`),
  ADD CONSTRAINT `interventions_ibfk_2` FOREIGN KEY (`trial_id`) REFERENCES `trials` (`id`);
