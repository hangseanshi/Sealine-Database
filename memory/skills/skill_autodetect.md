# Auto-Detect Input Type

## Tracking Number Detection

When the user enters a **single word with NO hyphen** (e.g., `00010987`, `038VH1276706`), treat it as a `TrackNumber` and perform a tracking status lookup:

1. Query `Sealine_Tracking` for this TrackNumber. The [Tracking Status] stores the current status of the TrackNumber.
2. Generate a tracking route map using `show_tracking_routes`.
3. **STOP HERE.** Do NOT query any other tables, do NOT generate container maps.
4. At the END of your response, add a 'Follow-up options' section with these clickable options:
   - **List all containers for this tracking.**
   - **Show me the details of tracking route.**
   - **Show me the containers route on the map.**
   - **Show me the container searoute (ocean way) route on the map.**
5. When the user picks a follow-up:
   - 'List all containers': `SELECT DISTINCT [Container Name], [Container ISO Code], [Container Size Type] FROM Sealine_Container_Event WHERE TrackNumber='...'`. Title: '<N> container(s) in this shipment'.
   - 'details of tracking route': Query `Sealine_Tracking` for Pre-POL/POL/POD/Post-POD cities, dates, and isActual status.
   - 'containers route on the map': Generate container route map using `show_container_routes`.
   - 'container searoute': Generate container searoute map using `show_container_searoute`.

---

## Container Number Detection

When the user enters a word **WITH a hyphen** (e.g., `038NY1332530-TRHU7525920`), treat it as a container number:

1. The string before the LAST hyphen is the TrackNumber (e.g., `038NY1332530`).
2. Query `Sealine_Tracking` for the TrackNumber. Show header info with expanded status (same rules as above).
3. Show container details: `[Container Name]`, `[Container ISO Code]`, `[Container Size Type]` from `Sealine_Container_Event` (use DISTINCT).
4. Show all container events from `Sealine_Container_Event WHERE [Container Name]='...' ORDER BY [Event Sequence ID] ASC`.
5. Generate a container searoute map using `show_container_searoute` with this container number.
