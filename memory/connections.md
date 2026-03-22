# Named Database Connections

## sealineDB

- **Type:** SQL Server
- **Server:** ushou102-exap1
- **Database:** ai
- **Username:** sean
- **Password:** 4peiling
- **Driver:** ODBC Driver 17 for SQL Server

### Python connection snippet

```python
import pyodbc

conn = pyodbc.connect(
    'DRIVER={ODBC Driver 17 for SQL Server};'
    'SERVER=ushou102-exap1;'
    'DATABASE=ai;'
    'UID=sean;'
    'PWD=4peiling;'
)
```
