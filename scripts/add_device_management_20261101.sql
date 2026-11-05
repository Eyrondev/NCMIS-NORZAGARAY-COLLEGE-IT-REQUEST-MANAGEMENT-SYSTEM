-- ============================================================
-- Manage Device module — schema additions
-- Run once against the ncmis database.
-- The application also applies these automatically on first
-- visit to /manage_device (see utils/device_audit.py).
-- ============================================================

-- 1) Track where a device is used (a specific PC, or a department only).
ALTER TABLE `devices_full`
  ADD COLUMN `used_on_pcid` int(11) DEFAULT NULL AFTER `department_id`,
  ADD COLUMN `usage_scope` varchar(20) NOT NULL DEFAULT 'UNASSIGNED' AFTER `used_on_pcid`,
  ADD INDEX `idx_devices_used_on_pcid` (`used_on_pcid`);

-- 2) Field-level change log used by the Manage Device report tab.
CREATE TABLE IF NOT EXISTS `device_change_logs` (
  `log_id` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  `device_id` int(11) NOT NULL,
  `item_name` varchar(255) DEFAULT NULL,
  `serial_no` varchar(255) DEFAULT NULL,
  `action` varchar(40) NOT NULL,
  `field_name` varchar(100) DEFAULT NULL,
  `old_value` text DEFAULT NULL,
  `new_value` text DEFAULT NULL,
  `remarks` text DEFAULT NULL,
  `changed_by` int(11) DEFAULT NULL,
  `changed_by_name` varchar(255) DEFAULT NULL,
  `changed_at` datetime NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`log_id`),
  KEY `idx_device_change_device` (`device_id`),
  KEY `idx_device_change_changed_at` (`changed_at`),
  KEY `idx_device_change_action` (`action`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
