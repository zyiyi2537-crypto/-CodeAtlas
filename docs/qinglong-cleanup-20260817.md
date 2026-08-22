# Qinglong Cleanup Audit — 2026-08-17

- Local migration backup: `D:\qinglong\server-migration-20260817-145733`
- Integrity: 28 of 28 SHA-256 entries verified
- Backup size: 3.506 GB
- Database contents confirmed: 8 subscriptions, 39 environment variables,
  925 tasks
- Removed runtime: Qinglong container and `whyour/qinglong:2.17` / `latest`
- Released port: `15700`
- Released root-disk space: approximately 1.75 GiB
- Preserved instance:
  `/www/dk_project/dk_app/qinglong-retained/qinglong_5xfW-20260817-163007`
  (approximately 199 MB)
- Preserved services: Docker, `baota_net`, SSH, Baota panel on `8888`

The Alibaba Cloud security-group inbound rule for port `15700` cannot be removed
from the server and must be deleted in the cloud console.
