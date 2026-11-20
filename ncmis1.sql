-- phpMyAdmin SQL Dump
-- version 5.2.3
-- https://www.phpmyadmin.net/
--
-- Host: 127.0.0.1
-- Generation Time: Nov 05, 2026 at 01:34 PM
-- Server version: 10.11.16-MariaDB
-- PHP Version: 8.5.12

SET SQL_MODE = "NO_AUTO_VALUE_ON_ZERO";
START TRANSACTION;
SET time_zone = "+00:00";


/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!40101 SET NAMES utf8mb4 */;

--
-- Database: `ncmis`
--

-- --------------------------------------------------------

--
-- Table structure for table `asset_maintenance_logs`
--

CREATE TABLE `asset_maintenance_logs` (
  `id` int(11) NOT NULL,
  `asset_type` enum('PC','Device') DEFAULT NULL,
  `asset_id` int(11) DEFAULT NULL,
  `action` varchar(100) DEFAULT NULL,
  `previous_status` varchar(50) DEFAULT NULL,
  `new_status` varchar(50) DEFAULT NULL,
  `performed_by` varchar(255) DEFAULT NULL,
  `performed_at` timestamp NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `borrow_requests`
--

CREATE TABLE `borrow_requests` (
  `borrow_id` int(11) NOT NULL,
  `student_id` varchar(20) DEFAULT NULL,
  `last_name` varchar(50) DEFAULT NULL,
  `first_name` varchar(50) DEFAULT NULL,
  `middle_initial` varchar(5) DEFAULT NULL,
  `device_id` int(11) NOT NULL,
  `borrow_date` date NOT NULL,
  `return_date` date DEFAULT NULL,
  `reason` text NOT NULL,
  `status` enum('Pending','Approved','Returned','Cancelled','Invalid') DEFAULT 'Pending'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `concerns`
--

CREATE TABLE `concerns` (
  `concern_id` int(11) NOT NULL,
  `description` text NOT NULL,
  `status` enum('Pending','Ongoing','Resolved') DEFAULT 'Pending',
  `created_at` timestamp NOT NULL DEFAULT current_timestamp(),
  `updated_at` timestamp NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  `resolution_feedback` text DEFAULT NULL,
  `user_id` int(11) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `concern_devices`
--

CREATE TABLE `concern_devices` (
  `concern_id` int(11) NOT NULL,
  `device_id` int(11) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `concern_history`
--

CREATE TABLE `concern_history` (
  `id` int(11) NOT NULL,
  `concern_id` int(11) NOT NULL,
  `user_id` int(11) NOT NULL,
  `description` text NOT NULL,
  `status` enum('Pending','Ongoing','Resolved') NOT NULL,
  `created_at` datetime NOT NULL,
  `updated_at` datetime NOT NULL,
  `resolution_feedback` text NOT NULL,
  `faculty_name` varchar(255) NOT NULL,
  `email` varchar(255) DEFAULT NULL,
  `devices` text DEFAULT NULL,
  `archived_at` timestamp NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `consumables`
--

CREATE TABLE `consumables` (
  `accession_id` int(11) NOT NULL,
  `item_name` varchar(255) NOT NULL,
  `category` varchar(100) DEFAULT NULL,
  `brand` varchar(100) DEFAULT NULL,
  `quantity` int(11) DEFAULT 0,
  `unit` varchar(50) DEFAULT NULL,
  `department_id` int(11) DEFAULT NULL,
  `location` varchar(255) DEFAULT NULL,
  `status` enum('Available','Low Stock','Out of Stock','Damaged') DEFAULT 'Available',
  `description` text DEFAULT NULL,
  `date_added` datetime DEFAULT current_timestamp(),
  `last_updated` datetime DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  `added_by` varchar(255) DEFAULT NULL,
  `is_archived` tinyint(1) DEFAULT 0,
  `deleted_at` datetime DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `consumable_transactions`
--

CREATE TABLE `consumable_transactions` (
  `transaction_id` int(11) NOT NULL,
  `accession_id` int(11) NOT NULL,
  `item_name` varchar(255) DEFAULT NULL,
  `action` enum('RECEIVE','RETURN') NOT NULL,
  `quantity` int(11) NOT NULL,
  `previous_stock` int(11) DEFAULT NULL,
  `new_stock` int(11) DEFAULT NULL,
  `reference_no` varchar(100) DEFAULT NULL,
  `reason` varchar(255) DEFAULT NULL,
  `notes` text DEFAULT NULL,
  `performed_by` int(11) DEFAULT NULL,
  `created_at` timestamp NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `consumable_usage`
--

CREATE TABLE `consumable_usage` (
  `id` int(11) NOT NULL,
  `accession_id` int(11) NOT NULL,
  `item_name` varchar(255) DEFAULT NULL,
  `quantity` int(11) NOT NULL,
  `previous_stock` int(11) DEFAULT NULL,
  `new_stock` int(11) DEFAULT NULL,
  `reference_no` varchar(100) DEFAULT NULL,
  `reason` varchar(255) DEFAULT NULL,
  `notes` text DEFAULT NULL,
  `performed_by` int(11) DEFAULT NULL,
  `department_id` int(11) DEFAULT NULL,
  `created_at` timestamp NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `damage_reports`
--

CREATE TABLE `damage_reports` (
  `id` int(11) NOT NULL,
  `pcid` int(11) NOT NULL,
  `reported_by` varchar(255) DEFAULT NULL,
  `damage_type` varchar(255) DEFAULT NULL,
  `description` text DEFAULT NULL,
  `date_reported` datetime DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `damage_types`
--

CREATE TABLE `damage_types` (
  `damage_type_id` int(11) NOT NULL,
  `name` varchar(100) NOT NULL,
  `description` text DEFAULT NULL,
  `severity_level` enum('Low','Medium','High','Critical') DEFAULT 'Low',
  `created_at` timestamp NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `departments`
--

CREATE TABLE `departments` (
  `department_id` int(11) NOT NULL,
  `department_name` varchar(100) NOT NULL,
  `department_code` varchar(10) DEFAULT NULL,
  `category` varchar(50) NOT NULL DEFAULT 'other',
  `max_pc_allowed` int(10) UNSIGNED NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

--
-- Dumping data for table `departments`
--

INSERT INTO `departments` (`department_id`, `department_name`, `department_code`, `category`, `max_pc_allowed`) VALUES
(1, 'Dean\'s Office', 'DO', 'Office', 0),
(3, 'MIS OFFICE', NULL, 'Office', 0),
(25, 'CLC', NULL, 'Laboratory', 0),
(29, 'CLB', NULL, 'Laboratory', 0),
(31, 'Registar', NULL, 'Office', 10),
(32, 'Clinic', NULL, 'Facility', 2),
(33, 'CLA INVENTORY', NULL, 'Laboratory', 60),
(34, 'CLB INVENTORY', NULL, 'Laboratory', 50),
(35, 'CLC INVENTORY', NULL, 'Laboratory', 50);

-- --------------------------------------------------------

--
-- Table structure for table `devices`
--

CREATE TABLE `devices` (
  `device_id` int(11) NOT NULL,
  `item_name` varchar(150) NOT NULL,
  `brand_model` varchar(100) DEFAULT NULL,
  `department_id` int(11) DEFAULT NULL,
  `serial_number` varchar(100) DEFAULT NULL,
  `quantity` int(11) DEFAULT 0,
  `device_type` varchar(50) DEFAULT NULL,
  `status` enum('Available','Borrowed','Damaged','Disposed') DEFAULT 'Available'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `devices_full`
--

CREATE TABLE `devices_full` (
  `accession_id` int(11) NOT NULL,
  `device_id` int(11) DEFAULT NULL,
  `item_name` varchar(255) NOT NULL,
  `brand_model` varchar(255) DEFAULT NULL,
  `quantity` int(11) DEFAULT 1,
  `acquisition_cost` decimal(12,2) DEFAULT NULL,
  `date_acquired` date DEFAULT NULL,
  `accountable` varchar(255) DEFAULT NULL,
  `serial_no` varchar(255) NOT NULL,
  `municipal_serial_no` varchar(255) DEFAULT NULL,
  `device_type` varchar(255) DEFAULT NULL,
  `department_id` int(11) DEFAULT NULL,
  `status` varchar(50) DEFAULT 'Available',
  `created_at` timestamp NOT NULL DEFAULT current_timestamp(),
  `updated_at` timestamp NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  `last_checked` date DEFAULT NULL,
  `maintenance_interval_days` int(11) DEFAULT 30,
  `health_score` int(11) DEFAULT 100,
  `risk_level` varchar(20) DEFAULT 'Low',
  `is_archived` tinyint(1) DEFAULT 0,
  `deleted_at` datetime DEFAULT NULL,
  `used_on_pcid` int(11) DEFAULT NULL,
  `usage_scope` varchar(20) NOT NULL DEFAULT 'UNASSIGNED'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

--
-- Dumping data for table `devices_full`
--

INSERT INTO `devices_full` (`accession_id`, `device_id`, `item_name`, `brand_model`, `quantity`, `acquisition_cost`, `date_acquired`, `accountable`, `serial_no`, `municipal_serial_no`, `device_type`, `department_id`, `status`, `created_at`, `updated_at`, `last_checked`, `maintenance_interval_days`, `health_score`, `risk_level`, `is_archived`, `deleted_at`, `used_on_pcid`, `usage_scope`) VALUES
(999906, NULL, 'Motherboard', 'ASUS', 0, 5000.00, '2026-10-29', 'John Doe', 'SN42382583', 'MSN42382561', 'Device', 33, 'IN USE', '2026-10-29 14:17:03', '2026-11-01 09:55:24', NULL, 1825, 100, 'Low', 0, NULL, NULL, 'UNASSIGNED'),
(999907, NULL, 'Motherboard', 'ASUS', 0, 5000.00, '2026-10-29', 'John Doe', 'SN42382648', 'MSN42382643', 'Device', 33, 'IN USE', '2026-10-29 14:17:03', '2026-11-01 09:56:15', NULL, 1825, 100, 'Low', 0, NULL, NULL, 'UNASSIGNED'),
(999908, NULL, 'Motherboard', 'ASUS', 0, 5000.00, '2026-10-29', 'John Doe', 'SN42382621', 'MSN42382627', 'Device', 33, 'IN USE', '2026-10-29 14:17:03', '2026-11-05 12:18:09', NULL, 1825, 100, 'Low', 0, NULL, NULL, 'UNASSIGNED');

-- --------------------------------------------------------

--
-- Table structure for table `devices_units`
--

CREATE TABLE `devices_units` (
  `accession_id` int(11) NOT NULL,
  `device_id` int(11) NOT NULL,
  `serial_number` varchar(100) DEFAULT NULL,
  `status` enum('Available','Borrowed','Damaged','Disposed') DEFAULT 'Available'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `device_change_logs`
--

CREATE TABLE `device_change_logs` (
  `log_id` bigint(20) UNSIGNED NOT NULL,
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
  `changed_at` datetime NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `device_damage_reports`
--

CREATE TABLE `device_damage_reports` (
  `id` int(11) NOT NULL,
  `accession_id` int(11) DEFAULT NULL,
  `reported_by` varchar(255) DEFAULT NULL,
  `damage_type` varchar(255) DEFAULT NULL,
  `description` text DEFAULT NULL,
  `date_reported` datetime DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `inventory_audit_log`
--

CREATE TABLE `inventory_audit_log` (
  `audit_id` bigint(20) UNSIGNED NOT NULL,
  `entity_type` enum('PC','DEVICE') NOT NULL,
  `entity_id` int(11) NOT NULL,
  `action` enum('CREATE','UPDATE','DELETE','SOFT_DELETE','RESTORE','BULK_UPDATE','STATUS_CHANGE','MAINTENANCE','CHECKED') NOT NULL,
  `field_name` varchar(100) DEFAULT NULL,
  `old_value` text DEFAULT NULL,
  `new_value` text DEFAULT NULL,
  `performed_by` int(11) NOT NULL,
  `performed_at` datetime NOT NULL DEFAULT current_timestamp(),
  `ip_address` varchar(45) DEFAULT NULL,
  `user_agent` varchar(255) DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

--
-- Dumping data for table `inventory_audit_log`
--

INSERT INTO `inventory_audit_log` (`audit_id`, `entity_type`, `entity_id`, `action`, `field_name`, `old_value`, `new_value`, `performed_by`, `performed_at`, `ip_address`, `user_agent`) VALUES
(1, 'DEVICE', 999901, 'UPDATE', 'quantity', '0', '1', 1, '2026-10-29 22:10:09', NULL, NULL),
(2, 'DEVICE', 999901, 'UPDATE', 'status', 'IN USE', 'Damaged', 1, '2026-10-29 22:10:09', NULL, NULL),
(3, 'DEVICE', 999905, 'UPDATE', 'quantity', '1', '0', 1, '2026-10-29 22:10:09', NULL, NULL),
(4, 'DEVICE', 999905, 'UPDATE', 'status', 'Available', 'IN USE', 1, '2026-10-29 22:10:09', NULL, NULL),
(5, 'DEVICE', 999903, 'UPDATE', 'quantity', '4', '5', 1, '2026-10-29 22:10:09', NULL, NULL),
(6, 'DEVICE', 999904, 'UPDATE', 'quantity', '0', '1', 1, '2026-10-29 22:10:09', NULL, NULL),
(7, 'DEVICE', 999904, 'UPDATE', 'status', 'IN USE', 'Damaged', 1, '2026-10-29 22:10:09', NULL, NULL),
(8, 'DEVICE', 999901, 'UPDATE', 'quantity', '0', '1', 1, '2026-10-29 22:10:35', NULL, NULL),
(9, 'DEVICE', 999901, 'UPDATE', 'status', 'IN USE', 'Damaged', 1, '2026-10-29 22:10:35', NULL, NULL),
(10, 'DEVICE', 999905, 'UPDATE', 'quantity', '1', '0', 1, '2026-10-29 22:10:35', NULL, NULL),
(11, 'DEVICE', 999905, 'UPDATE', 'status', 'Available', 'IN USE', 1, '2026-10-29 22:10:35', NULL, NULL),
(12, 'DEVICE', 999903, 'UPDATE', 'quantity', '4', '5', 1, '2026-10-29 22:10:35', NULL, NULL),
(13, 'DEVICE', 999904, 'UPDATE', 'quantity', '0', '1', 1, '2026-10-29 22:10:35', NULL, NULL),
(14, 'DEVICE', 999904, 'UPDATE', 'status', 'IN USE', 'Damaged', 1, '2026-10-29 22:10:35', NULL, NULL),
(15, 'DEVICE', 999902, 'UPDATE', 'status', 'Available', 'Damaged', 1, '2026-10-29 22:10:35', NULL, NULL),
(35, 'PC', 999903, 'UPDATE', 'motherboard', 'gigabyte', 'Motherboard — ASUS', 9, '2026-10-29 22:17:47', NULL, NULL),
(60, 'DEVICE', 999906, 'UPDATE', 'quantity', '1', '0', 9, '2026-11-01 17:55:24', NULL, NULL),
(61, 'DEVICE', 999906, 'UPDATE', 'status', 'Available', 'IN USE', 9, '2026-11-01 17:55:24', NULL, NULL),
(62, 'DEVICE', 999907, 'UPDATE', 'quantity', '1', '0', 9, '2026-11-01 17:56:15', NULL, NULL),
(63, 'DEVICE', 999907, 'UPDATE', 'status', 'Available', 'IN USE', 9, '2026-11-01 17:56:15', NULL, NULL),
(64, 'DEVICE', 999908, 'UPDATE', 'quantity', '1', '0', 9, '2026-11-05 20:18:09', NULL, NULL),
(65, 'DEVICE', 999908, 'UPDATE', 'status', 'Available', 'IN USE', 9, '2026-11-05 20:18:09', NULL, NULL);

-- --------------------------------------------------------

--
-- Table structure for table `inventory_settings`
--

CREATE TABLE `inventory_settings` (
  `id` int(11) NOT NULL,
  `auto_check_days` int(11) NOT NULL DEFAULT 0,
  `enabled` tinyint(1) DEFAULT 1,
  `updated_at` timestamp NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `inventory_status_logs`
--

CREATE TABLE `inventory_status_logs` (
  `id` int(11) NOT NULL,
  `item_type` enum('PC','Device') DEFAULT NULL,
  `item_id` int(11) DEFAULT NULL,
  `old_status` varchar(50) DEFAULT NULL,
  `new_status` varchar(50) DEFAULT NULL,
  `changed_at` timestamp NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `maintenance_history`
--

CREATE TABLE `maintenance_history` (
  `id` int(11) NOT NULL,
  `pcid` int(11) DEFAULT NULL,
  `asset_type` enum('PC','Device') NOT NULL,
  `asset_id` int(11) NOT NULL,
  `action` varchar(100) NOT NULL,
  `maintenance_type` varchar(100) DEFAULT NULL,
  `replaced_part` varchar(100) DEFAULT NULL,
  `previous_part` text DEFAULT NULL,
  `new_part` text DEFAULT NULL,
  `previous_part_status` varchar(100) DEFAULT NULL,
  `new_part_status` varchar(100) DEFAULT NULL,
  `computer_name` varchar(255) DEFAULT NULL,
  `asset_serial` varchar(255) DEFAULT NULL,
  `technician` varchar(255) DEFAULT NULL,
  `remarks` text DEFAULT NULL,
  `performed_by` varchar(255) DEFAULT NULL,
  `old_status` varchar(100) DEFAULT NULL,
  `new_status` varchar(100) DEFAULT NULL,
  `risk_level` varchar(20) DEFAULT NULL,
  `health_score` int(11) DEFAULT NULL,
  `performed_at` timestamp NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

--
-- Triggers `maintenance_history`
--
DELIMITER $$
CREATE TRIGGER `trg_maintenance_history_immutable_update` BEFORE UPDATE ON `maintenance_history` FOR EACH ROW BEGIN
  SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Maintenance history is immutable and cannot be updated.';
END
$$
CREATE TRIGGER `trg_maintenance_history_immutable_delete` BEFORE DELETE ON `maintenance_history` FOR EACH ROW BEGIN
  SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Maintenance history is immutable and cannot be deleted.';
END
$$
DELIMITER ;

-- --------------------------------------------------------

--
-- Table structure for table `maintenance_history_backfill_20260402`
--

CREATE TABLE `maintenance_history_backfill_20260402` (
  `id` int(11) NOT NULL,
  `pcid` int(11) DEFAULT NULL,
  `asset_type` enum('PC','Device') NOT NULL,
  `asset_id` int(11) NOT NULL,
  `action` varchar(100) NOT NULL,
  `remarks` text DEFAULT NULL,
  `performed_by` varchar(255) DEFAULT NULL,
  `old_status` varchar(100) DEFAULT NULL,
  `new_status` varchar(100) DEFAULT NULL,
  `risk_level` varchar(20) DEFAULT NULL,
  `health_score` int(11) DEFAULT NULL,
  `performed_at` timestamp NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `maintenance_logs`
--

CREATE TABLE `maintenance_logs` (
  `id` int(11) NOT NULL,
  `asset_type` enum('PC','DEVICE') NOT NULL,
  `asset_id` int(11) NOT NULL,
  `previous_status` varchar(100) DEFAULT NULL,
  `new_status` varchar(100) DEFAULT NULL,
  `previous_risk_level` varchar(20) DEFAULT NULL,
  `new_risk_level` varchar(20) DEFAULT NULL,
  `action` varchar(255) NOT NULL,
  `performed_by` varchar(255) DEFAULT NULL,
  `remarks` text DEFAULT NULL,
  `performed_at` timestamp NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `notifications`
--

CREATE TABLE `notifications` (
  `id` int(11) NOT NULL,
  `user_id` int(11) NOT NULL,
  `action` varchar(255) NOT NULL,
  `target_type` varchar(50) DEFAULT NULL,
  `target_id` int(11) DEFAULT NULL,
  `created_at` timestamp NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `pcinfofull`
--

CREATE TABLE `pcinfofull` (
  `pcid` int(11) NOT NULL,
  `pcname` varchar(255) NOT NULL,
  `department_id` int(11) DEFAULT NULL,
  `location` varchar(255) DEFAULT NULL,
  `quantity` int(11) NOT NULL DEFAULT 1,
  `acquisition_cost` decimal(12,2) DEFAULT NULL,
  `date_acquired` date DEFAULT NULL,
  `accountable` varchar(255) DEFAULT NULL,
  `serial_no` varchar(255) NOT NULL,
  `municipal_serial_no` varchar(255) DEFAULT NULL,
  `status` varchar(100) DEFAULT NULL,
  `note` text DEFAULT NULL,
  `monitor` varchar(255) DEFAULT NULL,
  `motherboard` varchar(255) DEFAULT NULL,
  `ram` varchar(255) DEFAULT NULL,
  `storage` varchar(255) DEFAULT NULL,
  `gpu` varchar(255) DEFAULT NULL,
  `psu` varchar(255) DEFAULT NULL,
  `casing` varchar(255) DEFAULT NULL,
  `other_parts` text DEFAULT NULL,
  `created_at` timestamp NOT NULL DEFAULT current_timestamp(),
  `updated_at` timestamp NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  `last_checked` date DEFAULT NULL,
  `maintenance_interval_days` int(11) DEFAULT 30,
  `health_score` int(11) DEFAULT 100,
  `risk_level` varchar(20) DEFAULT 'Low',
  `is_archived` tinyint(1) DEFAULT 0,
  `deleted_at` datetime DEFAULT NULL,
  `mouse` text DEFAULT NULL,
  `keyboard` text DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

--
-- Dumping data for table `pcinfofull`
--

INSERT INTO `pcinfofull` (`pcid`, `pcname`, `department_id`, `location`, `quantity`, `acquisition_cost`, `date_acquired`, `accountable`, `serial_no`, `municipal_serial_no`, `status`, `note`, `monitor`, `motherboard`, `ram`, `storage`, `gpu`, `psu`, `casing`, `other_parts`, `created_at`, `updated_at`, `last_checked`, `maintenance_interval_days`, `health_score`, `risk_level`, `is_archived`, `deleted_at`, `mouse`, `keyboard`) VALUES
(999903, 'pc-1', 33, NULL, 1, 35000.00, '2026-10-29', 'John Doe', 'SN-1793283308476-893922', 'MSN-1793283308476-790400', 'Available', '', 'nvision', 'Motherboard — ASUS', 'ramsta', 'ramsta ssd', 'n/a', 'corsair', 'stock', '', '2026-10-29 14:16:11', '2026-10-29 14:17:47', '2026-10-29', 1825, 100, 'Low', 0, NULL, 'garuda', 'inplay');

--
-- Triggers `pcinfofull`
--
DELIMITER $$
CREATE TRIGGER `before_insert_pcinfofull` BEFORE INSERT ON `pcinfofull` FOR EACH ROW BEGIN
  DECLARE next_id INT;
  IF NEW.serial_no IS NULL OR NEW.serial_no = '' THEN
    SELECT IFNULL(MAX(pcid), 0) + 1 INTO next_id FROM pcinfofull;
    SET NEW.serial_no = CONCAT('SN-', LPAD(next_id, 6, '0'));
  END IF;
END
$$
DELIMITER ;

-- --------------------------------------------------------

--
-- Table structure for table `pcparts`
--

CREATE TABLE `pcparts` (
  `part_id` int(11) NOT NULL,
  `pcid` varchar(50) NOT NULL,
  `monitor` varchar(100) DEFAULT NULL,
  `motherboard` varchar(100) DEFAULT NULL,
  `ram` varchar(100) DEFAULT NULL,
  `storage` varchar(100) DEFAULT NULL,
  `gpu` varchar(100) DEFAULT NULL,
  `psu` varchar(100) DEFAULT NULL,
  `casing` varchar(100) DEFAULT NULL,
  `other_parts` text DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `pcs`
--

CREATE TABLE `pcs` (
  `pcid` varchar(50) NOT NULL,
  `pcname` varchar(100) NOT NULL,
  `department_id` int(11) DEFAULT NULL,
  `status` enum('Active','In Repair','Decommissioned') DEFAULT 'Active',
  `note` text DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `pc_part_replacements`
--

CREATE TABLE `pc_part_replacements` (
  `replacement_id` int(11) NOT NULL,
  `pcid` int(11) NOT NULL,
  `part` varchar(64) NOT NULL,
  `old_value` text DEFAULT NULL,
  `new_value` text DEFAULT NULL,
  `removed_status` varchar(32) DEFAULT NULL,
  `accession_id` int(11) DEFAULT NULL,
  `new_accession_id` int(11) DEFAULT NULL,
  `performed_by` int(11) DEFAULT NULL,
  `remarks` text DEFAULT NULL,
  `performed_at` timestamp NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

--
-- Dumping data for table `pc_part_replacements`
--

INSERT INTO `pc_part_replacements` (`replacement_id`, `pcid`, `part`, `old_value`, `new_value`, `removed_status`, `accession_id`, `performed_by`, `remarks`, `performed_at`) VALUES
(4, 990710, 'gpu', 'GPU — NVIDIA RTX 3060', 'GPU — NVIDIA RTX 4060', 'Damaged', 990021, 1, NULL, '2026-10-29 14:29:58'),
(5, 990710, 'motherboard', 'E2E-OLD-SERIAL-SN', 'Motherboard — MSI MAG B760', 'Damaged', 990010, 1, NULL, '2026-10-29 14:29:58'),
(6, 990710, 'psu', 'E2E-SHARED-SN', 'PSU — Seasonic 750W', 'Damaged', NULL, 1, NULL, '2026-10-29 14:29:58'),
(7, 990710, 'ram', 'RAM — Kingston 8GB', 'RAM — Kingston 16GB', 'Damaged', 990011, 1, NULL, '2026-10-29 14:29:58');

-- --------------------------------------------------------

--
-- Table structure for table `students`
--

CREATE TABLE `students` (
  `student_id` varchar(20) NOT NULL,
  `last_name` varchar(50) NOT NULL,
  `first_name` varchar(50) NOT NULL,
  `middle_initial` varchar(5) DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `tickets`
--

CREATE TABLE `tickets` (
  `ticket_id` int(11) NOT NULL,
  `user_id` int(11) DEFAULT NULL,
  `department_id` int(11) DEFAULT NULL,
  `device_id` int(11) DEFAULT NULL,
  `title` varchar(255) DEFAULT NULL,
  `description` text DEFAULT NULL,
  `status` varchar(50) DEFAULT 'Open',
  `created_at` datetime DEFAULT current_timestamp(),
  `resolved_at` datetime DEFAULT NULL,
  `resolved_by_id` int(11) DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `ticket_devices`
--

CREATE TABLE `ticket_devices` (
  `id` int(11) NOT NULL,
  `ticket_id` int(11) NOT NULL,
  `device_id` int(11) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `ticket_responses`
--

CREATE TABLE `ticket_responses` (
  `id` int(11) NOT NULL,
  `ticket_id` int(11) NOT NULL,
  `responder_id` int(11) DEFAULT NULL,
  `action` varchar(50) DEFAULT NULL,
  `feedback` text DEFAULT NULL,
  `created_at` datetime DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `users`
--

CREATE TABLE `users` (
  `user_id` int(10) UNSIGNED NOT NULL,
  `username` varchar(50) NOT NULL,
  `faculty_name` varchar(255) NOT NULL,
  `email` varchar(255) NOT NULL,
  `password` varchar(255) NOT NULL,
  `is_admin` tinyint(1) NOT NULL DEFAULT 0,
  `role` varchar(50) DEFAULT 'staff',
  `is_active` tinyint(1) NOT NULL DEFAULT 1,
  `created_at` timestamp NOT NULL DEFAULT current_timestamp(),
  `updated_at` timestamp NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  `permissions` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL CHECK (json_valid(`permissions`)),
  `first_name` varchar(50) DEFAULT NULL,
  `middle_name` varchar(50) DEFAULT NULL,
  `last_name` varchar(50) DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

--
-- Dumping data for table `users`
--

INSERT INTO `users` (`user_id`, `username`, `faculty_name`, `email`, `password`, `is_admin`, `role`, `is_active`, `created_at`, `updated_at`, `permissions`, `first_name`, `middle_name`, `last_name`) VALUES
(9, 'admin', 'santos, matthew S.', 'matthewjohnsantos2004@gmail.com', 'scrypt:32768:8:1$ryLUIe9RsrDae4ph$d62414102427f2911938fc93a342514c4064c148112be99a3aa808a83bf62eca63b5ab43f9b0cc8341e01f21f8968882b1eb26c91b737a8872e9950ff25befd0', 1, 'admin', 1, '2025-03-24 02:34:16', '2026-04-17 09:09:20', '{\"dashboard\": {\"view\": true, \"edit\": true}, \"inventory\": {\"view\": true, \"edit\": true}, \"qrlist\": {\"view\": true, \"edit\": true}, \"report\": {\"view\": true, \"edit\": true}, \"dept\": {\"view\": true, \"edit\": false}}', 'matthew', 's', 'santos'),
(10, 'user', 'me', 'matthewjohnsantos143@gmail.com', 'scrypt:32768:8:1$LQ4mixntjhzilHyY$7e9f133cb8da3b06625ac3ee3164c9d8fb97983226c64ddfecddad0fa9fd76a5821f391e3d1595c4029544ae4a3f253a0952f1496174f3b46093503766ff5fcb', 0, 'staff', 1, '2025-03-24 09:25:58', '2026-04-16 09:22:54', '{\"dashboard\": {\"view\": true, \"edit\": false}, \"inventory\": {\"view\": true, \"edit\": false}, \"qrlist\": {\"view\": true, \"edit\": false}, \"report\": {\"view\": true, \"edit\": false}, \"dept\": {\"view\": true, \"edit\": false}}', 'matthew', 's', 'santos'),
(11, 'rbautista', 'BA, Re', 'renatobautista17@gmail.com', 'scrypt:32768:8:1$Gc5JBZzptDeAvn9b$cf555f0d8ead0898962845e3fba4f6ef99fc3ab7e7c0a4f86f09d766054ed4dcfdc99174d15db62bad4701f583feb1fea555bcd14f9593675ee30e3b7891a037', 1, 'admin', 1, '2025-10-26 15:24:49', '2026-04-17 09:09:20', '{\"dashboard\": {\"view\": true, \"edit\": true}, \"inventory\": {\"view\": true, \"edit\": true}, \"qrlist\": {\"view\": true, \"edit\": true}, \"report\": {\"view\": true, \"edit\": true}, \"dept\": {\"view\": true, \"edit\": true}}', 'Renato', 'sd', 'Bautista'),
(12, 'submit_test', '', 'submit_test@example.com', 'scrypt:32768:8:1$t8DS2ZyE49aADG24$9ed78f270bd77cdb726f80393936381864cf579d0fa613ba44c789040cc35d49c031a0646fc1af1831e4d0b8be0c0ee03cb71ad84c397a58414d65704a999fbc', 0, 'staff', 1, '2026-04-17 08:45:40', '2026-04-17 08:45:40', '{\"dashboard\": {\"view\": true, \"edit\": false}, \"inventory\": {\"view\": true, \"edit\": false}, \"transaction\": {\"view\": true, \"add\": true, \"edit\": false}, \"submit_ticket\": {\"view\": true, \"add\": true}, \"qrlist\": {\"view\": true, \"edit\": false}, \"report\": {\"view\": true, \"edit\": false}, \"dept\": {\"view\": true, \"edit\": false, \"delete\": false}, \"damage_report\": {\"view\": false, \"edit\": false}, \"receive_item\": {\"view\": false, \"edit\": false}, \"activity_log\": {\"view\": false, \"edit\": false}, \"manage_user\": {\"view\": false, \"edit\": false}, \"maintenance\": {\"view\": false, \"edit\": false}, \"stock_room\": {\"view\": false, \"edit\": false}}', 'Submit', '', 'Tester'),
(13, 'user_test', '', 'user_test@example.com', 'scrypt:32768:8:1$vThdfItbXbS5itGf$fceb658e98fc6ed541398c22d37fc0ec5a997d05d7d850c7c8a9d30c1c50af079fc77cb6805a94e813dee4c05ce6ec41e5167d8b9a32337d93dcef1169dfc120', 0, 'staff', 1, '2026-04-17 09:09:27', '2026-04-17 09:12:52', '{\"dashboard\": {\"view\": false, \"edit\": false}, \"inventory\": {\"view\": false, \"edit\": false}, \"transaction\": {\"view\": true, \"add\": true, \"edit\": false}, \"submit_ticket\": {\"view\": true, \"add\": true}, \"qrlist\": {\"view\": true, \"edit\": false}, \"report\": {\"view\": true, \"edit\": false}, \"dept\": {\"view\": true, \"edit\": false, \"delete\": false}, \"damage_report\": {\"view\": false, \"edit\": false}, \"receive_item\": {\"view\": false, \"edit\": false}, \"activity_log\": {\"view\": false, \"edit\": false}, \"manage_user\": {\"view\": false, \"edit\": false}, \"maintenance\": {\"view\": false, \"edit\": false}, \"stock_room\": {\"view\": false, \"edit\": false}}', 'User', '', 'Tester'),
(14, 'working_user', '', 'working_user@example.com', 'scrypt:32768:8:1$cz9EWqWVTQU75jdZ$30fc180069445f837ebc68ec5f9eb67baaaf9e6b885064781d4aee457f7bb1f5dceb85219f97d412b148995f93a973b3143eb290c539b63f2e52aa6a84cb23ba', 0, 'user', 1, '2026-04-17 09:14:39', '2026-04-17 09:14:39', '{\"dashboard\": {\"view\": true, \"edit\": false}, \"inventory\": {\"view\": true, \"edit\": false}, \"transaction\": {\"view\": true, \"add\": true, \"edit\": false}, \"submit_ticket\": {\"view\": true, \"add\": true}, \"qrlist\": {\"view\": true, \"edit\": false}, \"report\": {\"view\": true, \"edit\": false}, \"dept\": {\"view\": true, \"edit\": false, \"delete\": false}, \"damage_report\": {\"view\": false, \"edit\": false}, \"receive_item\": {\"view\": false, \"edit\": false}, \"activity_log\": {\"view\": false, \"edit\": false}, \"manage_user\": {\"view\": false, \"edit\": false}, \"maintenance\": {\"view\": false, \"edit\": false}, \"stock_room\": {\"view\": false, \"edit\": false}}', 'Working', '', 'User');

-- --------------------------------------------------------

--
-- Table structure for table `user_activity_log`
--

CREATE TABLE `user_activity_log` (
  `log_id` bigint(20) UNSIGNED NOT NULL,
  `user_id` int(10) UNSIGNED DEFAULT NULL,
  `username` varchar(100) NOT NULL,
  `role` varchar(20) NOT NULL,
  `action` varchar(120) NOT NULL,
  `module` varchar(120) DEFAULT NULL,
  `details` text DEFAULT NULL,
  `http_method` varchar(10) DEFAULT NULL,
  `route` varchar(255) DEFAULT NULL,
  `ip_address` varchar(45) DEFAULT NULL,
  `created_at` timestamp NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

--
-- Dumping data for table `user_activity_log`
--

INSERT INTO `user_activity_log` (`log_id`, `user_id`, `username`, `role`, `action`, `module`, `details`, `http_method`, `route`, `ip_address`, `created_at`) VALUES
(1, 9, 'admin', 'Admin', 'View Page', 'Manage Inventory', 'Visited /manage_inventory?section=pc', 'GET', '/manage_inventory', '127.0.0.1', '2026-10-29 14:02:48'),
(2, 1, 'e2e-tester', 'Admin', 'Submit', 'Manage Pc', 'POST /replace-pc-part -> 200', 'POST', '/replace-pc-part', '127.0.0.1', '2026-10-29 14:12:21'),
(3, 1, 'e2e-tester', 'Admin', 'Submit', 'Manage Pc', 'POST /replace-pc-part -> 200', 'POST', '/replace-pc-part', '127.0.0.1', '2026-10-29 14:12:33'),
(4, 1, 'e2e', 'Admin', 'Submit', 'Manage Pc', 'POST /replace-pc-part -> 200', 'POST', '/replace-pc-part', '127.0.0.1', '2026-10-29 14:13:24'),
(5, 9, 'admin', 'Admin', 'View Page', 'Manage Inventory', 'Visited /manage_inventory?section=pc', 'GET', '/manage_inventory', '127.0.0.1', '2026-10-29 14:15:05'),
(6, 9, 'admin', 'Admin', 'Add Item', 'PC Management', 'POST /add-pcinfofull -> 200', 'POST', '/add-pcinfofull', '127.0.0.1', '2026-10-29 14:16:11'),
(7, 9, 'admin', 'Admin', 'View Page', 'Manage Inventory', 'Visited /manage_inventory?section=pc', 'GET', '/manage_inventory', '127.0.0.1', '2026-10-29 14:16:13'),
(8, 9, 'admin', 'Admin', 'Add Item', 'Item Management', 'POST /manage_inventory/add-device -> 302', 'POST', '/manage_inventory/add-device', '127.0.0.1', '2026-10-29 14:17:03'),
(9, 9, 'admin', 'Admin', 'View Page', 'Manage Inventory', 'Visited /manage_inventory', 'GET', '/manage_inventory', '127.0.0.1', '2026-10-29 14:17:03'),
(10, 9, 'admin', 'Admin', 'View Page', 'Manage Inventory', 'Visited /manage_inventory?section=item', 'GET', '/manage_inventory', '127.0.0.1', '2026-10-29 14:17:07'),
(11, 9, 'admin', 'Admin', 'Submit', 'Manage Pc', 'POST /replace-pc-part -> 200', 'POST', '/replace-pc-part', '127.0.0.1', '2026-10-29 14:17:47'),
(12, 9, 'admin', 'Admin', 'View Page', 'Manage Inventory', 'Visited /manage_inventory?section=item', 'GET', '/manage_inventory', '127.0.0.1', '2026-10-29 14:17:50'),
(13, 9, 'admin', 'Admin', 'Submit', 'Manage Pc', 'POST /replace-pc-part -> 200', 'POST', '/replace-pc-part', '127.0.0.1', '2026-10-29 14:18:03'),
(14, 9, 'admin', 'Admin', 'View Page', 'Manage Inventory', 'Visited /manage_inventory?section=pc', 'GET', '/manage_inventory', '127.0.0.1', '2026-10-29 14:18:06'),
(15, 9, 'admin', 'Admin', 'View Page', 'Manage Department', 'Visited /manage-department', 'GET', '/manage-department', '127.0.0.1', '2026-10-29 14:21:32'),
(16, 9, 'admin', 'Admin', 'View Page', 'Manage User', 'Visited /manage-user', 'GET', '/manage-user', '127.0.0.1', '2026-10-29 14:21:36'),
(17, 9, 'admin', 'Admin', 'View Page', 'Activity Log', 'Visited /activity-log', 'GET', '/activity-log', '127.0.0.1', '2026-10-29 14:21:38'),
(18, 9, 'admin', 'Admin', 'View Page', 'Ticket Reports', 'Visited /tickets/report', 'GET', '/tickets/report', '127.0.0.1', '2026-10-29 14:21:41'),
(19, 9, 'admin', 'Admin', 'View Page', 'Maintenance', 'Visited /maintenance/history', 'GET', '/maintenance/history', '127.0.0.1', '2026-10-29 14:21:45'),
(20, 9, 'admin', 'Admin', 'View Page', 'Transaction', 'Visited /transaction_history', 'GET', '/transaction_history', '127.0.0.1', '2026-10-29 14:21:50'),
(21, 9, 'admin', 'Admin', 'View Page', 'Manage Department', 'Visited /manage-department', 'GET', '/manage-department', '127.0.0.1', '2026-10-29 14:21:55'),
(22, 9, 'admin', 'Admin', 'View Page', 'Ticket Reports', 'Visited /tickets/report', 'GET', '/tickets/report', '127.0.0.1', '2026-10-29 14:21:56'),
(23, 9, 'admin', 'Admin', 'View Page', 'Maintenance', 'Visited /maintenance/history', 'GET', '/maintenance/history', '127.0.0.1', '2026-10-29 14:21:59'),
(24, 9, 'admin', 'Admin', 'View Page', 'Transaction', 'Visited /transactions', 'GET', '/transactions', '127.0.0.1', '2026-10-29 14:22:52'),
(25, 9, 'admin', 'Admin', 'View Page', 'Manage Inventory', 'Visited /manage_inventory', 'GET', '/manage_inventory', '127.0.0.1', '2026-10-29 14:22:53'),
(26, 1, 'e2e', 'Admin', 'Submit', 'Manage Pc', 'POST /replace-pc-part -> 200', 'POST', '/replace-pc-part', '127.0.0.1', '2026-10-29 14:29:36'),
(27, 1, 'e2e', 'Admin', 'View Page', 'Manage Pc', 'Visited /pc-replacement-history?pcid=990720', 'GET', '/pc-replacement-history', '127.0.0.1', '2026-10-29 14:29:36'),
(28, 1, 'e2e', 'Admin', 'Submit', 'Manage Pc', 'POST /replace-pc-part -> 200', 'POST', '/replace-pc-part', '127.0.0.1', '2026-10-29 14:29:46'),
(29, 1, 'e2e', 'Admin', 'View Page', 'Manage Pc', 'Visited /pc-replacement-history?pcid=990720', 'GET', '/pc-replacement-history', '127.0.0.1', '2026-10-29 14:29:46'),
(30, 1, 'e2e', 'Admin', 'Submit', 'Manage Pc', 'POST /replace-pc-part -> 200', 'POST', '/replace-pc-part', '127.0.0.1', '2026-10-29 14:29:46'),
(31, 1, 'e2e', 'Admin', 'Submit', 'Manage Pc', 'POST /replace-pc-part -> 200', 'POST', '/replace-pc-part', '127.0.0.1', '2026-10-29 14:29:58'),
(32, 9, 'admin', 'Admin', 'Login', 'Authentication', 'User signed in successfully', 'POST', '/login/', '127.0.0.1', '2026-11-01 09:54:51'),
(33, 9, 'admin', 'Admin', 'View Page', 'Dashboard', 'Visited /dashboardload', 'GET', '/dashboardload', '127.0.0.1', '2026-11-01 09:54:52'),
(34, 9, 'admin', 'Admin', 'View Page', 'Transaction', 'Visited /transactions', 'GET', '/transactions', '127.0.0.1', '2026-11-01 09:54:57'),
(35, 9, 'admin', 'Admin', 'View Page', 'Manage Inventory', 'Visited /manage_inventory', 'GET', '/manage_inventory', '127.0.0.1', '2026-11-01 09:54:59'),
(36, 9, 'admin', 'Admin', 'View Page', 'Manage Pc', 'Visited /pc-replacement-history?pcid=999903', 'GET', '/pc-replacement-history', '127.0.0.1', '2026-11-01 09:55:10'),
(37, 9, 'admin', 'Admin', 'View Page', 'Manage Inventory', 'Visited /manage_inventory?section=pc', 'GET', '/manage_inventory', '127.0.0.1', '2026-11-01 09:55:12'),
(38, 9, 'admin', 'Admin', 'Submit', 'Manage Pc', 'POST /replace-pc-part -> 200', 'POST', '/replace-pc-part', '127.0.0.1', '2026-11-01 09:55:24'),
(39, 9, 'admin', 'Admin', 'View Page', 'Manage Pc', 'Visited /pc-replacement-history?pcid=999903', 'GET', '/pc-replacement-history', '127.0.0.1', '2026-11-01 09:55:26'),
(40, 9, 'admin', 'Admin', 'View Page', 'Manage Inventory', 'Visited /manage_inventory?section=pc', 'GET', '/manage_inventory', '127.0.0.1', '2026-11-01 09:55:28'),
(41, 9, 'admin', 'Admin', 'View Page', 'Manage Inventory', 'Visited /manage_inventory?section=pc', 'GET', '/manage_inventory', '127.0.0.1', '2026-11-01 09:55:29'),
(42, 9, 'admin', 'Admin', 'View Page', 'Manage Pc', 'Visited /pc-replacement-history?pcid=999903', 'GET', '/pc-replacement-history', '127.0.0.1', '2026-11-01 09:55:31'),
(43, 9, 'admin', 'Admin', 'View Page', 'Manage Inventory', 'Visited /manage_inventory?section=pc', 'GET', '/manage_inventory', '127.0.0.1', '2026-11-01 09:55:33'),
(44, 9, 'admin', 'Admin', 'View Page', 'Manage Pc', 'Visited /pc-replacement-history?pcid=999903', 'GET', '/pc-replacement-history', '127.0.0.1', '2026-11-01 09:55:52'),
(45, 9, 'admin', 'Admin', 'View Page', 'Manage Inventory', 'Visited /manage_inventory?section=pc', 'GET', '/manage_inventory', '127.0.0.1', '2026-11-01 09:55:55'),
(46, 9, 'admin', 'Admin', 'Submit', 'Manage Pc', 'POST /replace-pc-part -> 200', 'POST', '/replace-pc-part', '127.0.0.1', '2026-11-01 09:56:15'),
(47, 9, 'admin', 'Admin', 'View Page', 'Manage Inventory', 'Visited /manage_inventory?section=item', 'GET', '/manage_inventory', '127.0.0.1', '2026-11-01 09:56:19'),
(48, 9, 'admin', 'Admin', 'Login', 'Authentication', 'User signed in successfully', 'POST', '/login/', '127.0.0.1', '2026-11-05 12:16:40'),
(49, 9, 'admin', 'Admin', 'View Page', 'Dashboard', 'Visited /dashboardload', 'GET', '/dashboardload', '127.0.0.1', '2026-11-05 12:16:40'),
(50, 9, 'admin', 'Admin', 'View Page', 'Manage Device', 'Visited /manage_device', 'GET', '/manage_device', '127.0.0.1', '2026-11-05 12:17:15'),
(51, 9, 'admin', 'Admin', 'View Page', 'Manage Inventory', 'Visited /manage_inventory', 'GET', '/manage_inventory', '127.0.0.1', '2026-11-05 12:17:18'),
(52, 9, 'admin', 'Admin', 'View Page', 'Manage Device', 'Visited /manage_device', 'GET', '/manage_device', '127.0.0.1', '2026-11-05 12:17:20'),
(53, 9, 'admin', 'Admin', 'View Page', 'Manage Device', 'Visited /manage_device?tab=report', 'GET', '/manage_device', '127.0.0.1', '2026-11-05 12:17:30'),
(54, 9, 'admin', 'Admin', 'View Page', 'Manage Device', 'Visited /manage_device?tab=inventory', 'GET', '/manage_device', '127.0.0.1', '2026-11-05 12:17:34'),
(55, 9, 'admin', 'Admin', 'View Page', 'Manage Inventory', 'Visited /manage_inventory', 'GET', '/manage_inventory', '127.0.0.1', '2026-11-05 12:17:55'),
(56, 9, 'admin', 'Admin', 'Submit', 'Manage Pc', 'POST /replace-pc-part -> 200', 'POST', '/replace-pc-part', '127.0.0.1', '2026-11-05 12:18:09'),
(57, 9, 'admin', 'Admin', 'View Page', 'Manage Device', 'Visited /manage_device', 'GET', '/manage_device', '127.0.0.1', '2026-11-05 12:18:12'),
(58, 9, 'admin', 'Admin', 'View Page', 'Manage Inventory', 'Visited /manage_inventory', 'GET', '/manage_inventory', '127.0.0.1', '2026-11-05 12:19:27'),
(59, 9, 'admin', 'Admin', 'View Page', 'Manage Pc', 'Visited /pc-replacement-history?pcid=999903', 'GET', '/pc-replacement-history', '127.0.0.1', '2026-11-05 12:19:34'),
(60, 9, 'admin', 'Admin', 'View Page', 'Manage Inventory', 'Visited /manage_inventory?section=pc', 'GET', '/manage_inventory', '127.0.0.1', '2026-11-05 12:19:36'),
(61, 9, 'admin', 'Admin', 'View Page', 'Manage Device', 'Visited /manage_device', 'GET', '/manage_device', '127.0.0.1', '2026-11-05 12:20:47'),
(62, 9, 'admin', 'Admin', 'View Page', 'Dashboard', 'Visited /dashboardload', 'GET', '/dashboardload', '127.0.0.1', '2026-11-05 12:20:52'),
(63, 9, 'admin', 'Admin', 'View Page', 'Manage Inventory', 'Visited /manage_inventory', 'GET', '/manage_inventory', '127.0.0.1', '2026-11-05 12:23:51'),
(64, 9, 'admin', 'Admin', 'View Page', 'Manage Inventory', 'Visited /manage_inventory', 'GET', '/manage_inventory', '127.0.0.1', '2026-11-05 12:27:05'),
(65, 9, 'admin', 'Admin', 'View Page', 'Manage Device', 'Visited /manage_device', 'GET', '/manage_device', '127.0.0.1', '2026-11-05 12:28:18'),
(66, 9, 'admin', 'Admin', 'View Page', 'Manage Device', 'Visited /manage_device?tab=report', 'GET', '/manage_device', '127.0.0.1', '2026-11-05 12:29:14'),
(67, 9, 'admin', 'Admin', 'View Page', 'Manage Device', 'Visited /manage_device', 'GET', '/manage_device', '127.0.0.1', '2026-11-05 12:30:48');

--
-- Indexes for dumped tables
--

--
-- Indexes for table `asset_maintenance_logs`
--
ALTER TABLE `asset_maintenance_logs`
  ADD PRIMARY KEY (`id`);

--
-- Indexes for table `borrow_requests`
--
ALTER TABLE `borrow_requests`
  ADD PRIMARY KEY (`borrow_id`),
  ADD KEY `fk_borrow_device` (`device_id`);

--
-- Indexes for table `concerns`
--
ALTER TABLE `concerns`
  ADD PRIMARY KEY (`concern_id`);

--
-- Indexes for table `concern_devices`
--
ALTER TABLE `concern_devices`
  ADD PRIMARY KEY (`concern_id`,`device_id`),
  ADD KEY `device_id` (`device_id`);

--
-- Indexes for table `concern_history`
--
ALTER TABLE `concern_history`
  ADD PRIMARY KEY (`id`);

--
-- Indexes for table `consumables`
--
ALTER TABLE `consumables`
  ADD PRIMARY KEY (`accession_id`),
  ADD KEY `department_id` (`department_id`);

--
-- Indexes for table `consumable_transactions`
--
ALTER TABLE `consumable_transactions`
  ADD PRIMARY KEY (`transaction_id`),
  ADD KEY `accession_id` (`accession_id`);

--
-- Indexes for table `consumable_usage`
--
ALTER TABLE `consumable_usage`
  ADD PRIMARY KEY (`id`);

--
-- Indexes for table `damage_reports`
--
ALTER TABLE `damage_reports`
  ADD PRIMARY KEY (`id`),
  ADD KEY `pcid` (`pcid`);

--
-- Indexes for table `damage_types`
--
ALTER TABLE `damage_types`
  ADD PRIMARY KEY (`damage_type_id`);

--
-- Indexes for table `departments`
--
ALTER TABLE `departments`
  ADD PRIMARY KEY (`department_id`);

--
-- Indexes for table `devices`
--
ALTER TABLE `devices`
  ADD PRIMARY KEY (`device_id`),
  ADD KEY `department_id` (`department_id`);

--
-- Indexes for table `devices_full`
--
ALTER TABLE `devices_full`
  ADD PRIMARY KEY (`accession_id`),
  ADD UNIQUE KEY `serial_no` (`serial_no`),
  ADD UNIQUE KEY `municipal_serial_no` (`municipal_serial_no`),
  ADD KEY `fk_department` (`department_id`);

--
-- Indexes for table `devices_units`
--
ALTER TABLE `devices_units`
  ADD PRIMARY KEY (`accession_id`),
  ADD KEY `device_id` (`device_id`);

--
-- Indexes for table `device_change_logs`
--
ALTER TABLE `device_change_logs`
  ADD PRIMARY KEY (`log_id`),
  ADD KEY `idx_device_change_device` (`device_id`),
  ADD KEY `idx_device_change_changed_at` (`changed_at`),
  ADD KEY `idx_device_change_action` (`action`);

--
-- Indexes for table `device_damage_reports`
--
ALTER TABLE `device_damage_reports`
  ADD PRIMARY KEY (`id`),
  ADD KEY `accession_id` (`accession_id`);

--
-- Indexes for table `inventory_audit_log`
--
ALTER TABLE `inventory_audit_log`
  ADD PRIMARY KEY (`audit_id`),
  ADD KEY `idx_entity` (`entity_type`,`entity_id`),
  ADD KEY `idx_action` (`action`),
  ADD KEY `idx_user` (`performed_by`),
  ADD KEY `idx_date` (`performed_at`);

--
-- Indexes for table `inventory_settings`
--
ALTER TABLE `inventory_settings`
  ADD PRIMARY KEY (`id`);

--
-- Indexes for table `inventory_status_logs`
--
ALTER TABLE `inventory_status_logs`
  ADD PRIMARY KEY (`id`);

--
-- Indexes for table `maintenance_history`
--
ALTER TABLE `maintenance_history`
  ADD PRIMARY KEY (`id`),
  ADD KEY `fk_maintenance_pc` (`pcid`);

--
-- Indexes for table `maintenance_history_backfill_20260402`
--
ALTER TABLE `maintenance_history_backfill_20260402`
  ADD PRIMARY KEY (`id`),
  ADD KEY `fk_maintenance_pc` (`pcid`);

--
-- Indexes for table `maintenance_logs`
--
ALTER TABLE `maintenance_logs`
  ADD PRIMARY KEY (`id`);

--
-- Indexes for table `notifications`
--
ALTER TABLE `notifications`
  ADD PRIMARY KEY (`id`);

--
-- Indexes for table `pcinfofull`
--
ALTER TABLE `pcinfofull`
  ADD PRIMARY KEY (`pcid`),
  ADD UNIQUE KEY `pcname` (`pcname`),
  ADD UNIQUE KEY `serial_no` (`serial_no`),
  ADD UNIQUE KEY `municipal_serial_no` (`municipal_serial_no`),
  ADD KEY `department_id` (`department_id`);

--
-- Indexes for table `pcparts`
--
ALTER TABLE `pcparts`
  ADD PRIMARY KEY (`part_id`),
  ADD KEY `pcid` (`pcid`);

--
-- Indexes for table `pcs`
--
ALTER TABLE `pcs`
  ADD PRIMARY KEY (`pcid`),
  ADD KEY `department_id` (`department_id`);

--
-- Indexes for table `pc_part_replacements`
--
ALTER TABLE `pc_part_replacements`
  ADD PRIMARY KEY (`replacement_id`),
  ADD KEY `idx_pc_part_replacements_pcid` (`pcid`);

--
-- Indexes for table `students`
--
ALTER TABLE `students`
  ADD PRIMARY KEY (`student_id`);

--
-- Indexes for table `tickets`
--
ALTER TABLE `tickets`
  ADD PRIMARY KEY (`ticket_id`);

--
-- Indexes for table `ticket_devices`
--
ALTER TABLE `ticket_devices`
  ADD PRIMARY KEY (`id`);

--
-- Indexes for table `ticket_responses`
--
ALTER TABLE `ticket_responses`
  ADD PRIMARY KEY (`id`);

--
-- Indexes for table `users`
--
ALTER TABLE `users`
  ADD PRIMARY KEY (`user_id`),
  ADD UNIQUE KEY `username` (`username`);

--
-- Indexes for table `user_activity_log`
--
ALTER TABLE `user_activity_log`
  ADD PRIMARY KEY (`log_id`),
  ADD KEY `idx_user_activity_created_at` (`created_at`),
  ADD KEY `idx_user_activity_role` (`role`),
  ADD KEY `idx_user_activity_user_id` (`user_id`);

--
-- AUTO_INCREMENT for dumped tables
--

--
-- AUTO_INCREMENT for table `asset_maintenance_logs`
--
ALTER TABLE `asset_maintenance_logs`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `borrow_requests`
--
ALTER TABLE `borrow_requests`
  MODIFY `borrow_id` int(11) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `concerns`
--
ALTER TABLE `concerns`
  MODIFY `concern_id` int(11) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `concern_history`
--
ALTER TABLE `concern_history`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `consumables`
--
ALTER TABLE `consumables`
  MODIFY `accession_id` int(11) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `consumable_transactions`
--
ALTER TABLE `consumable_transactions`
  MODIFY `transaction_id` int(11) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `consumable_usage`
--
ALTER TABLE `consumable_usage`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `damage_reports`
--
ALTER TABLE `damage_reports`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `damage_types`
--
ALTER TABLE `damage_types`
  MODIFY `damage_type_id` int(11) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `departments`
--
ALTER TABLE `departments`
  MODIFY `department_id` int(11) NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=36;

--
-- AUTO_INCREMENT for table `devices`
--
ALTER TABLE `devices`
  MODIFY `device_id` int(11) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `devices_full`
--
ALTER TABLE `devices_full`
  MODIFY `accession_id` int(11) NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=999909;

--
-- AUTO_INCREMENT for table `devices_units`
--
ALTER TABLE `devices_units`
  MODIFY `accession_id` int(11) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `device_change_logs`
--
ALTER TABLE `device_change_logs`
  MODIFY `log_id` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `device_damage_reports`
--
ALTER TABLE `device_damage_reports`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `inventory_audit_log`
--
ALTER TABLE `inventory_audit_log`
  MODIFY `audit_id` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=66;

--
-- AUTO_INCREMENT for table `inventory_settings`
--
ALTER TABLE `inventory_settings`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `inventory_status_logs`
--
ALTER TABLE `inventory_status_logs`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `maintenance_history`
--
ALTER TABLE `maintenance_history`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `maintenance_history_backfill_20260402`
--
ALTER TABLE `maintenance_history_backfill_20260402`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `maintenance_logs`
--
ALTER TABLE `maintenance_logs`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `notifications`
--
ALTER TABLE `notifications`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `pcinfofull`
--
ALTER TABLE `pcinfofull`
  MODIFY `pcid` int(11) NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=999904;

--
-- AUTO_INCREMENT for table `pcparts`
--
ALTER TABLE `pcparts`
  MODIFY `part_id` int(11) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `pc_part_replacements`
--
ALTER TABLE `pc_part_replacements`
  MODIFY `replacement_id` int(11) NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=8;

--
-- AUTO_INCREMENT for table `tickets`
--
ALTER TABLE `tickets`
  MODIFY `ticket_id` int(11) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `ticket_devices`
--
ALTER TABLE `ticket_devices`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `ticket_responses`
--
ALTER TABLE `ticket_responses`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `users`
--
ALTER TABLE `users`
  MODIFY `user_id` int(10) UNSIGNED NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=15;

--
-- AUTO_INCREMENT for table `user_activity_log`
--
ALTER TABLE `user_activity_log`
  MODIFY `log_id` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=68;

--
-- Constraints for dumped tables
--

--
-- Constraints for table `borrow_requests`
--
ALTER TABLE `borrow_requests`
  ADD CONSTRAINT `fk_borrow_device` FOREIGN KEY (`device_id`) REFERENCES `devices` (`device_id`) ON DELETE CASCADE ON UPDATE CASCADE;

--
-- Constraints for table `concern_devices`
--
ALTER TABLE `concern_devices`
  ADD CONSTRAINT `concern_devices_ibfk_1` FOREIGN KEY (`concern_id`) REFERENCES `concerns` (`concern_id`) ON DELETE CASCADE,
  ADD CONSTRAINT `concern_devices_ibfk_2` FOREIGN KEY (`device_id`) REFERENCES `devices` (`device_id`);

--
-- Constraints for table `consumables`
--
ALTER TABLE `consumables`
  ADD CONSTRAINT `consumables_ibfk_1` FOREIGN KEY (`department_id`) REFERENCES `departments` (`department_id`);

--
-- Constraints for table `consumable_transactions`
--
ALTER TABLE `consumable_transactions`
  ADD CONSTRAINT `consumable_transactions_ibfk_1` FOREIGN KEY (`accession_id`) REFERENCES `consumables` (`accession_id`);

--
-- Constraints for table `devices`
--
ALTER TABLE `devices`
  ADD CONSTRAINT `devices_ibfk_1` FOREIGN KEY (`department_id`) REFERENCES `departments` (`department_id`);
COMMIT;

/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
