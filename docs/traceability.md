# Requirements traceability

Requirement -> module -> test. Kept current with every pull request (see the PR template).
Requirement text lives in [requirements.md](requirements.md).

| Requirement | Module(s) | Test(s) |
|-------------|-----------|---------|
| NFR-3 Auditability (append-only) | `talentsift/db.py`, `talentsift/audit.py` | `tests/test_db.py::test_audit_event_rejects_sql_update_and_delete`, `tests/test_db.py::test_audit_event_rejects_orm_update_and_delete` |
| NFR-8 Portability (settings) | `talentsift/config.py` | `tests/test_db.py::test_settings_from_env_parses_values` |

Rows for the remaining requirements are added as each build step lands.
