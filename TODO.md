Section 1: Dashboard Decluttering
Objective
Simplify the dashboard by removing excessive statistics and distributing them into their respective modules.
Revision Requirements
- Remove the large number of dashboard cards.
- Move module-specific analytics into their corresponding navigation pages.
- Each management module should display only statistics relevant to that module.
Example
Instead of showing device statistics on the main dashboard:
- Available Devices
- Needs Fixing
- Under Maintenance
- Borrowed
- Archived
These should appear inside Manage Device.

Apply the same concept to every module.
Expected Result
The main dashboard should only contain:
- Overall system summary
- Recent activities
- Notifications
- Quick shortcuts
- Global analytics
Each module should have its own mini dashboard.
Section 2: Inventory Navigation Restructure
Objective
Split the Inventory section into dedicated navigation pages instead of displaying everything on a single inventory dashboard.
Revision Requirements
Replace the current Inventory dashboard with individual navigation items.

Example:

Inventory
- Manage Devices
- Manage Components
- Manage Peripherals
- Manage Consumables
- Manage Accessories
Each page should contain:
- Its own statistics
- Filters
- Search
- CRUD operations
- Reports related only to that inventory category
Do not place all inventory summaries on one page.
Section 3: Part Replacement Workflow
Objective
Improve the replacement process so inventory updates automatically whenever a hardware component is replaced.
Revision Requirements
When replacing a component (example: Motherboard):
1. User clicks Replace Part.
2. Open a replacement dialog.
3. Select replacement component from available inventory.
4. Automatically update statuses.
Example:

Old Motherboard
- User chooses status after removal:
  - Available
  - Needs Fixing
  - Defective
  - For Disposal
Replacement Motherboard
- Automatically becomes Used.
- Assigned to the selected computer.
The system must prevent duplicate assignments.

Inventory quantities and statuses must update immediately after confirmation.
Section 4: Maintenance History
Objective
Automatically record every maintenance activity performed on a computer.
Revision Requirements
Every repair or replacement should generate a maintenance history record.

Include:
- Computer Name
- Asset ID
- Maintenance Date
- Technician
- Maintenance Type
- Replaced Part
- Previous Part
- New Part
- Previous Status
- New Status
- Remarks
History should be immutable after saving.
Section 5: Maintenance Report
Objective
Create a dedicated report page for completed maintenance activities.
Revision Requirements
Create a Maintenance Reports module that displays all maintenance records.

Each report should include:
- Computer Information
- Replaced Components
- Technician
- Date
- Maintenance Type
- Remarks
Provide:
- Search
- Filters
- Date Range
- Export to PDF
- Export to Excel
- Print
Example:

PC-001

Maintenance:
- Replaced Motherboard
- Installed 16GB RAM
- Replaced SSD
Technician:

John Doe

Date:

July 18, 2026

Status:

Completed