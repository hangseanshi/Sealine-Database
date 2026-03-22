## Core Data Model
**Sealine_Tracking (PK: TrackNumber)** — central shipment record
- TrackNumber: The gloal sealine tracking number.
- Sealine_Code: The sealine carrier code.
- Sealine_Name: The sealine carrier full name.
- Delivery_Number: SAP delivery number associated with the shipment.
- Release_Number: SAP Release number associated with the shipment.
- No Of Containers: Total number of containers in this tracking.
- Pre-POL City: The city of the Pre-POL (Pre-Port of Loading).
- Pre-POL State: The state/province of the Pre-POL (Pre-Port of Loading).
- Pre-POL Country: The full country name of the Pre-POL (Pre-Port of Loading).
- Pre-POL Country Code: The 2 characters country abbrivation of the Pre-POL (Pre-Port of Loading).
- Pre-POL Latitude: The Latitude of the Pre-POL (Pre-Port of Loading).
- Pre-POL Longitude: The Logitude of the Pre-POL (Pre-Port of Loading).
- Pre-POL LOCode: The unique port code for the Pre-POL (Pre-Port of Loading).
- Pre-POL Date: The date when tracking reaches of the Pre-POL (Pre-Port of Loading).
- Pre-POL isActual: The date is actual/confirmed of the Pre-POL (Pre-Port of Loading).
- Pre-POL Occurred: Yes/No value to indicates if the tracking actually reached the Pre-POL or not.
- POL City: The city of the POL (Port of Loading).
- POL State: The state/province of the POL (Port of Loading).
- POL Country: The full country name of the POL (Port of Loading).
- POL Country Code: The 2 characters country abbrivation of the POL (Port of Loading).
- POL Latitude: The Latitude of the POL (Port of Loading).
- POL Longitude: The Logitude of the POL (Port of Loading).
- POL LOCode: The unique port code for the POL (Port of Loading).
- POL Date: The date when tracking reaches of the POL (Port of Loading).
- POL isActual: The date is actual/confirmed of the POL (Port of Loading). If isActual=1, "POL Date" is called "ATD", if isActual=0, "POL Date" is called "ETD".
- POL Occurred: Yes/No value to indicates if the tracking actually reached the POL or not.
- POD City: The city of the POD (Port of Discharge).
- POD State: The state/province of the POD (Port of Discharge).
- POD Country: The full country name of the POD (Port of Discharge).
- POD Country Code: The 2 characters country abbrivation of the POD (Port of Discharge).
- POD Latitude: The Latitude of the POD (Port of Discharge).
- POD Longitude: The Logitude of the POD (Port of Discharge).
- POD LOCode: The unique port code for the POD (Port of Discharge).
- POD Date: The date when tracking reaches of the POD (Port of Discharge).
- POD isActual: The date is actual/confirmed of the POD (Port of Discharge). if isActual=1, "POD Date" is called "ATA', if isActual=0, "POD Date" is called "ETA".
- POD Occurred: Yes/No value to indicates if the tracking actually reached the POD or not.
- Post-POD City: The city of the Post-POD (Post-Port of Discharge).
- Post-POD State: The state/province of the Post-POD (Post-Port of Discharge).
- Post-POD Country: The full country name of the Post-POD (Post-Port of Discharge).
- Post-POD Country Code: The 2 characters country abbrivation of the Post-POD (Post-Port of Discharge).
- Post-POD Latitude: The Latitude of the Post-POD (Post-Port of Discharge).
- Post-POD Longitude: The Logitude of the Post-POD (Post-Port of Discharge).
- Post-POD LOCode: The unique port code for the Post-POD (Post-Port of Discharge).
- Post-POD Date: The date when tracking reaches of the Post-POD (Post-Port of Discharge).
- Post-POD isActual: The date is actual/confirmed of the Post-POD (Post-Port of Discharge).
- Post-POD Occurred: Yes/No value to indicates if the tracking actually reached the Post-POD or not.
- Tracking Status: Pending Departure,Arrived Destination,Delivered,Departed from Origin

**Sealine_Container_event (PK: TrackNumber,Container,Event Sequence ID)** — central container events for each TrackNumber
- TrackNumber: The gloal sealine tracking number, this is FK reference to Sealine_Tracking.TrackNumber
- Container Name: The name of the container.
- Container ISO Code: The ISO code for the container type.
- Container Size Type: The name of the container type.
- Event Sequence ID: The sequence of when event happens. The container event always happens from lower "Event Sequence ID" to higher "Event Sequence ID". For example, "Event Sequence ID"=1 is the first event of the container, "Event Sequence ID"=2 is the next event happens after "Event Sequence ID"=1.
- Location Name: The name of the location in where the event happens.
- Location Country Code: The 2 characters country code in where the event happens.
- Location LOCode: The port LOCode in where the event happens. In some of the cases, the LOCode may not be populated if the event happens in somewhere other than a standard port location.
- Location Latitude: The latitude in where the event happens.
- Location Longitude: The longitude in where the event happens.
- Event Description: The description of the event.
- Event Type: The type of the event.
- Event Code: The code of the event.
- Event Status: The status of the event.
- Event Date: The date when the event happens.
- Event Date isActual: Is the date of the event actual/confirmed.
- Transport Type: The event happens on land or sea
- Vessel Name: The name of the vessel if vessel involved in this event.
- Vessel Voyage: The voyage of the event.
- Location Type: The type of the location, it could be Pre-POL, POL, POD, Post-POD or any combination of these delimited by comma (,). This is to indicate the location is the tracking Pre-POL, POL, POD or Post-POD location.
- Event Occurred: Yes - The event already occured. No - Event not yet occur.

## Critical Implementation Notes
1. **Sealine_Tracking** 
- The Route of the tracking is always travel from Pre-POL -> POL -> POD -> Post-POD. Pre-POL and Post-POD is optional for the tracking. On the other hand, POL and POD are mandatory for all the tracking.
- "POL Location" should be display as "POL City"/"POL Country Code"("POL LOCode"). For example: Houston/US(USHOU). Use the same rule for "Pre-POL Location", "POD Location" and "Post-POD Location"
- Columns like "Pre-POL Occurred","POL Occurred","POD Occurred" and "Post-POD Occurred" is the indicator to identify where the actual location is. For example, if "POD Occurred"=Yes means the tracking is actually reached POD. To identify the latest actual location, we should search reversely from "Post-POD Occurred" -> "POD Occurred" -> "POL Occurred" -> "Pre-POL Occurred", the first Yes value indicate the latest location of the tracking.
2. **Sealine_Container_event** 
- When reference a container, we should use the format of "TrackNumber"-"Container Name". For example, the container name CAAU9988821 in TrackNumber 038VH9472368 should be always referenced as 038VH9472368-CAAU9988821
- The event of the container always happens from lower "Event Sequence ID" to higher "Event Sequence ID".
- Multiple event can happen at the same location.
- The container event is also indicate the route stop and route line of each container. The route stop will be the unique locations. The route line will be from a lower "Event Sequence ID" to immediate higher "Event Sequence ID". From example, the route is ways from "Event Sequence ID" 1->2->3->4 etc. The lower "Event Sequence ID" is also called Event Start Location. The immediate higher "Event Sequence ID" is called Event End Location.
- "Event Occurred" indicate if the event already happened or not. In order to identify the latest known container location, we can search from the highest "Container Sequence ID" to lower "Container Sequence ID" in sequence, the first record has the record with "Event Occurred"=Yes is the latest known location of the container.

