## Core Data Model

### Sealine_Tracking
**Primary Key:** TrackNumber

Central shipment record containing route stops and their status.

| Field | Description |
|-------|-------------|
| TrackNumber | Global sealine tracking number |
| Sealine_Code | Sealine carrier code |
| Sealine_Name | Sealine carrier full name |
| Delivery_Number | SAP delivery number associated with the shipment |
| Release_Number | SAP release number associated with the shipment |
| No Of Containers | Total number of containers in this tracking |
| Tracking Status | Current status of the tracking. Values: `Pending Departure`, `Departed from Origin`, `Arrived Destination`, `Delivered` |

#### Route Stop Fields
Each route stop (Pre-POL, POL, POD, Post-POD) shares the same set of fields. Pre-POL and Post-POD are optional; POL and POD are mandatory.

| Field | Description |
|-------|-------------|
| {Stop} City | City of the stop |
| {Stop} State | State/province of the stop |
| {Stop} Country | Full country name of the stop |
| {Stop} Country Code | 2-character country abbreviation |
| {Stop} Latitude | Latitude coordinate of the stop |
| {Stop} Longitude | Longitude coordinate of the stop |
| {Stop} LOCode | Unique port code of the stop |
| {Stop} Date | Date when the tracking reaches this stop. See date label rules below |
| {Stop} isActual | Whether the date is confirmed (`1`) or estimated (`0`). See date label rules below |
| {Stop} Occurred | Whether the tracking has physically reached this stop (`Yes` / `No`) |

**Date Label Rules based on `isActual`:**

| Stop | isActual = 1 (Actual) | isActual = 0 (Estimated) |
|------|-----------------------|--------------------------|
| POL Date | ATD (Actual Time of Departure) | ETD (Estimated Time of Departure) |
| POD Date | ATA (Actual Time of Arrival) | ETA (Estimated Time of Arrival) |
| Pre-POL Date | Actual Date | Estimated Date |
| Post-POD Date | Actual Date | Estimated Date |

---

### Sealine_Container_Event
**Primary Key:** TrackNumber, Container Name, Event Sequence ID

Container-level events for each TrackNumber, representing the detailed movement and activity of each container along its route.

| Field | Description |
|-------|-------------|
| TrackNumber | FK → `Sealine_Tracking.TrackNumber` |
| Container Name | Name/ID of the container |
| Container ISO Code | ISO code for the container type |
| Container Size Type | Descriptive name of the container type |
| Event Sequence ID | Chronological order of events; lower = earlier. Defines the route sequence: 1 → 2 → 3 → … |
| Location Name | Name of the location where the event occurs |
| Location Country Code | 2-character country code of the event location |
| Location LOCode | Port LOCode of the event location (may be empty for non-standard port locations) |
| Location Latitude | Latitude coordinate of the event location |
| Location Longitude | Longitude coordinate of the event location |
| Event Description | Description of the event |
| Event Type | Type/category of the event |
| Event Code | Code of the event |
| Event Status | Status of the event |
| Event Date | Date when the event occurs |
| Event Date isActual | Whether the event date is confirmed (`1`) or estimated (`0`) |
| Transport Type | Mode of transport for this event (`Land` / `Sea`) |
| Vessel Name | Name of the vessel involved, if applicable |
| Vessel Voyage | Voyage identifier, if applicable |
| Location Type | Route stop classification of this location: `Pre-POL`, `POL`, `POD`, `Post-POD`, or a comma-delimited combination. Blank or `TRANSIT` for intermediate stops |
| Event Occurred | Whether the event has already taken place (`Yes` / `No`) |

## Critical Implementation Notes

### 1. Sealine_Tracking

**Route Structure**
- The tracking route always follows this sequence: **Pre-POL → POL → POD → Post-POD**
- Pre-POL and Post-POD are **optional**; POL and POD are **mandatory** for all trackings.

**Location Display Format**
- Display all location fields using the format: `{City}/{Country Code}({LOCode})`
- Example: `Houston/US(USHOU)`
- Apply this format consistently to: Pre-POL Location, POL Location, POD Location, and Post-POD Location.

**Identifying the Latest Actual Location**
- The `*_Occurred` columns (Pre-POL Occurred, POL Occurred, POD Occurred, Post-POD Occurred) indicate whether the tracking has physically reached that stop.
- To find the latest actual location, scan in **reverse order** — Post-POD → POD → POL → Pre-POL — and return the first stop where `Occurred = Yes`.

**Displaying {Stop} Date**

Format: `{Stop Date in YYYY-MM-DD} {Date Indicator}`

**Date Indicator** is determined as follows:

| Condition | Indicator | Meaning |
|-----------|-----------|---------|
| `{Stop} isActual = 1` AND `{Stop} Occurred = Yes` | `[A]` | Actual, confirmed |
| `{Stop} isActual = 1` AND `{Stop} Occurred = No` | `(A)` | Actual date, not yet reached |
| `{Stop} isActual = 0` | `(E)` | Estimated |

**Examples:**
- `2025-03-01 [A]` — actual date, stop already reached
- `2025-03-01 (A)` — actual date, stop not yet reached
- `2025-03-01 (E)` — estimated date
---

### 2. Sealine_Container_Event

**Container Reference Format**
- Always reference a container using the format: `{TrackNumber}-{ContainerName}`
- Example: Container `CAAU9988821` under TrackNumber `038VH9472368` → `038VH9472368-CAAU9988821`

**Location Display Format**

Format: `{Location Name}/{Location Country Code}({Location Type}:{Location LOCode})`

| Field | Example |
|-------|---------|
| Single type | `Houston/US(POL:USHOU)` |
| Multiple types | `Houston/US(Pre-POL,POL:USHOU)` |

**Event Ordering**
- Events always occur in ascending order of **Event Sequence ID** (lower ID = earlier event).

**Determining if a Container Reached a Location**
- For a given TrackNumber, container, and location, check all associated events at that location:
  - If **any** event has `Event Occurred = Yes` → the container **has reached** that location.
  - If **no** event has `Event Occurred = Yes` → the container **has not reached** that location. If the previous location has at least one event with `Event Occurred = Yes`, the container is considered to have **departed from that previous location**.

**Route Stops and Route Lines**
- **Route Stops**: The unique locations derived from the container's events.
- **Route Lines**: Directional segments connecting consecutive Event Sequence IDs (e.g., 1→2, 2→3, 3→4).
  - The lower Event Sequence ID end is the **start location** (or Event Start Location).
  - The immediately higher Event Sequence ID end is the **end location** (or Event End Location).

**Identifying the Latest Known Container Location**
- Scan events from the **highest Event Sequence ID downward**; the first record with `Event Occurred = Yes` is the container's latest known location.

**Location Types**
- Containers share the same stop types as the TrackNumber: Pre-POL, POL, POD, Post-POD.
- Any additional intermediate stops are classified as **TRANSIT**.
- **Arrived at destination**: any event where `Location Type` contains `POD` and `Event Occurred = Yes`.
- **Departed from origin**: any event where `Location Type` contains `POL` (but **not** `Pre-POL`) and `Event Occurred = Yes`.

**Event Date**

Format: `{Event Date in YYYY-MM-DD} {Event Date Indicator}`

**Event Date Indicator** is determined as follows:

| Condition | Indicator | Meaning |
|-----------|-----------|---------|
| `Event Date isActual = 1` AND `Event Occurred = Yes` | `[A]` | Actual, confirmed |
| `Event Date isActual = 1` AND `Event Occurred = No` | `(A)` | Actual date, not yet reached |
| `Event Date isActual = 0` | `(E)` | Estimated |

**Examples:**
- `2025-03-01 [A]` — actual date, event already occurred
- `2025-03-01 (A)` — actual date, event not yet occurred
- `2025-03-01 (E)` — estimated date