# sealineDB Schema — searates database

## Connection
See connections.md for credentials.

## Relationships (logical, no FK constraints enforced in DB)

```
Sealine_Header.TrackNumber (PK)
    ├── 1:N → Sealine_Vessels.TrackNumber
    ├── 1:N → Sealine_Locations.TrackNumber
    ├── 1:N → Sealine_Container.TrackNumber
    ├── 1:N → Sealine_Container_Event.TrackNumber
    ├── 1:N → Sealine_Facilities.TrackNumber
    ├── 1:N → Sealine_Route.TrackNumber
    └── 1:N → Sealine_Tracking_Response.TrackNumber

Searates_Request_Tracking.TrackingNo → Sealine_Header.TrackNumber
ResponseLog.Id → Searates_Request_Tracking.LastResponseId
```

---

## Core Tables

### Sealine_Header
Primary tracking record per shipment.
| Column | Type | Notes |
|--------|------|-------|
| TrackNumber | varchar(100) NOT NULL | PK |
| Type | varchar(10) NOT NULL | e.g. BK (Booking) |
| Sealine_Code | varchar(100) NOT NULL | e.g. CMDU, LMCU, DHC2 |
| Sealine_Name | varchar(500) | e.g. CMA CGM, DHL Global Forwarding |
| API_Status | varchar(500) | success / error |
| Status | varchar(100) | IN_TRANSIT, DELIVERED, UNKNOWN, PLANNED, CANCELLED, COMPLETED |
| Is_Status_From_Sealine | int | 1=from carrier, 0=internal |
| Updated_Date | datetime | |
| CreatedOn | datetime | |
| UpdatedDT | datetime | |
| DeletedDt | datetime | soft delete |

### Sealine_Locations
Route stops per shipment.
| Column | Type | Notes |
|--------|------|-------|
| TrackNumber | varchar(100) NOT NULL | FK → Sealine_Header |
| Type | varchar(10) NOT NULL | |
| Sealine_Code | varchar(100) NOT NULL | |
| Name | varchar(1000) | Location name |
| State | varchar(500) | |
| Country | varchar(100) | |
| Country_Code | varchar(50) | |
| LOCode | varchar(100) | UN/LOCODE e.g. USHOU, SGSIN |
| Lat | varchar(100) | stored as string — use TRY_CAST(Lat AS FLOAT) |
| Lng | varchar(100) | stored as string — use TRY_CAST(Lng AS FLOAT) |
| Timezone | varchar(100) | |
| CreatedOn / UpdatedDT / DeletedDt | datetime | |

### Sealine_Vessels
Vessels associated with a shipment (1:N).
| Column | Type | Notes |
|--------|------|-------|
| TrackNumber | varchar(100) NOT NULL | FK → Sealine_Header |
| Type | varchar(10) NOT NULL | |
| Sealine_Code | varchar(100) NOT NULL | |
| Id | bigint NOT NULL | |
| Name | varchar(500) | Vessel name |
| imo | varchar(500) | IMO number |
| call_sign | varchar(500) | |
| mmsi | varchar(500) | |
| flag | varchar(100) | Country flag code |
| CreatedOn / UpdatedDT / DeletedDt | datetime | |

### Sealine_Container
All containers belonging to a shipment. Child of Sealine_Header via TrackNumber (1:N). A shipment can have multiple containers (~4 avg; 72,722 rows across 16,910 TrackNumbers).
| Column | Type | Notes |
|--------|------|-------|
| TrackNumber | varchar(100) NOT NULL | FK → Sealine_Header |
| Type | varchar(10) NOT NULL | e.g. `BK` (Booking) |
| Sealine_Code | varchar(100) NOT NULL | Carrier code |
| Container_NUMBER | varchar(100) NOT NULL | Container number; may be `UNKNOWN` |
| Iso_Code | varchar(100) | Container ISO type code |
| Size_Type | varchar(500) | e.g. `20GP`, `40HC` |
| Status | varchar(100) | e.g. `DELIVERED`, `IN_TRANSIT` |
| Is_Status_From_Sealine | int | 1 = from carrier, 0 = internal |
| CreatedOn | datetime | |
| UpdatedDT | datetime | |
| DeletedDt | datetime | Soft delete |

### Sealine_Container_Event
Container-level tracking events.
| Column | Type | Notes |
|--------|------|-------|
| TrackNumber | varchar(100) NOT NULL | |
| Sealine_Code / RequestType / Type | varchar | |
| Container_NUMBER | varchar(100) NOT NULL | |
| Order_id | varchar(100) NOT NULL | |
| Location / Facility | varchar(100) | |
| Description | varchar(100) | Event description |
| Event_type / Event_Code | varchar(100) | |
| Status | varchar(100) | |
| Date | datetime | Event date |
| Actual | int | 1=actual, 0=estimated |
| Is_Additional_Event | int | |
| Transport_Type | varchar(100) | |
| Vessel / Voyage | varchar(100) | |
| CreatedOn / UpdatedDT / DeletedDt | datetime | |

> Archive copies: Sealine_Container_Event_02May2025, Sealine_Container_Event_All, Sealine_Container_Event_Revised, Sealine_Container_Event_Revised_28APR2025

### Sealine_Route
Scheduled/actual route dates per location stop.
| Column | Type | Notes |
|--------|------|-------|
| TrackNumber | varchar(100) NOT NULL | FK → Sealine_Header |
| Type / Sealine_Code | varchar | |
| RouteType | varchar(100) NOT NULL | e.g. ETD, ETA, ATD, ATA |
| Location_Id | int | |
| Date | datetime | |
| IsActual | int | 1=actual, 0=planned |
| Predictive_ETA | varchar(100) | |
| CreatedOn / UpdatedDT / DeletedDt | datetime | |

### Sealine_Facilities
Port/terminal facilities per shipment.
| Column | Type | Notes |
|--------|------|-------|
| TrackNumber | varchar(100) NOT NULL | |
| Type / Sealine_Code | varchar | |
| Id | bigint NOT NULL | |
| name | varchar(1000) | |
| Country_Code | varchar(50) | |
| Locode / Bic_Code / Smdg_Code | varchar(100) | |
| Lat / lng | varchar(100) | |
| CreatedOn / UpdatedDT / DeletedDt | datetime | |

---

## Request / API Tables

### Searates_Request_Tracking
Active tracking requests sent to carriers.
| Column | Type | Notes |
|--------|------|-------|
| TrackingNo | varchar(100) NOT NULL | |
| RequestType | varchar(20) NOT NULL | |
| SealineCode | varchar(100) | |
| Carrier | varchar(100) NOT NULL | |
| Source_Name / Source_Record_Id | varchar(100) | Source system reference |
| Delivery_Number / Release_Number | varchar | |
| Batch | varchar(100) | |
| Tracking_Status | varchar(500) | |
| LastResponseId | bigint | → ResponseLog.Id |
| LastAPICallDate | datetime | |
| LastAPIStatus | varchar(100) | |
| CreatedDt | datetime | |
| isActive | bit | 1=active tracking |
| Message | varchar(5000) | |

> Searates_Request_Tracking_Deleted — archive of deleted requests (same schema)

### ResponseLog
API call log.
| Column | Type | Notes |
|--------|------|-------|
| Id | bigint NOT NULL | PK |
| TrackingNo | varchar(100) | |
| RequestType | varchar(10) | |
| Carrier | varchar(100) | |
| URL | varchar(max) | |
| API_Status | varchar(100) | |
| Tracking_Status | varchar(100) | |
| Response | varchar(max) | Raw JSON response |
| StartDate / EndDate | datetime | |
| RetryCnt | int | |
| Batch | varchar(500) | |

### Sealine_Tracking_Response
Batch tracking responses.
| Column | Type |
|--------|------|
| batch | varchar(100) |
| TrackNumber | varchar(100) |
| RequestType | varchar(100) |
| Carrier | varchar(100) |
| Response | varchar(max) |
| Status | varchar(100) |

---

## Reference / Mapping Tables

### API_Configuration
| Column | Notes |
|--------|-------|
| API_NAME | e.g. Sealine_Response |
| URL | Template URL with {key}, {number}, {type}, {sealine} placeholders |
| API_Key | K-23FF78E9-90DA-488C-978A-E98369E87695 |
| MaxRetryCnt | 10 |
| RetryInMin | 20 |
| Daily_API_Limit | 5000 |

### Carrier_Sealine_Mapaping
Maps carrier codes to sealine codes.
| Column | Type |
|--------|------|
| Carrier | varchar(100) |
| Sealine_Code | varchar(100) |
| isDefault | int |

### Response_Sealine_Mapping
Overrides sealine code from API response.
| Column | Type |
|--------|------|
| TrackingNo | varchar(100) |
| NewSealineCode | varchar(100) |

### _EDS_Shipline_code
External carrier code reference.
| Column | Type |
|--------|------|
| shipline_name | varchar(500) |
| carrier_code | varchar(10) |
| createdDT | datetime |

---

## Common Query Patterns

### Safe lat/lng cast
```sql
TRY_CAST(Lat AS FLOAT), TRY_CAST(Lng AS FLOAT)
```

### Mid-East war zone bounding boxes
```sql
-- Red Sea
(TRY_CAST(Lat AS FLOAT) BETWEEN 12 AND 28 AND TRY_CAST(Lng AS FLOAT) BETWEEN 32 AND 45)
-- Gulf of Aden
(TRY_CAST(Lat AS FLOAT) BETWEEN 10 AND 16 AND TRY_CAST(Lng AS FLOAT) BETWEEN 42 AND 52)
-- Persian Gulf
(TRY_CAST(Lat AS FLOAT) BETWEEN 22 AND 30 AND TRY_CAST(Lng AS FLOAT) BETWEEN 48 AND 60)
-- Eastern Mediterranean
(TRY_CAST(Lat AS FLOAT) BETWEEN 29 AND 38 AND TRY_CAST(Lng AS FLOAT) BETWEEN 28 AND 37)
```
