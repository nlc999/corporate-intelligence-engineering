# Case Study 06 — Containerisation, Automation & Cloud Archival

**CV evidence:** Docker, Docker Compose, Bash, PostgreSQL operational tooling and historical AWS S3 implementation.

This is a **curated, isolated adaptation** of existing engineering work, not an unchanged service or a claim that cloud infrastructure is currently deployed. All supplied companies and partition names are invented. The original implementation used a Node/Vite dashboard build stage plus Python/FastAPI and PostgreSQL services; this standalone showcase packages only the Python code that genuinely exists in this repository.

## Problem and design

Operational data products require repeatable environments, reliable database readiness, predictable command-line execution and safeguards against data loss. A credible archival procedure must distinguish a *successful upload*, a *verified byte-for-byte archive*, and a *restorability test*. This demonstration checks data integrity but does not perform a PostgreSQL restore test. For the isolated demonstration, source data deletion is deliberately excluded.

~~~mermaid
flowchart TD
    A[Synthetic company records] --> B[Bash bootstrap]
    B --> C[Existing PostgreSQL Compose service]
    B --> D[Multi-stage Python tools image]
    D --> E[Indexed discovery query]
    C --> E
    F[Historical partition archive design] --> G[Validate eligibility and export with pg_dump]
    G --> H[SHA-256 digest and content-addressed S3 key]
    H --> I[S3 encrypted upload]
    I --> J[HEAD metadata + stream downloaded bytes]
    J --> K{Size and SHA-256 agree?}
    K -->|Yes| L[Verified audit receipt]
    K -->|No| M[Fail closed]
    L --> N[Keep original PostgreSQL partition]
    M --> N
~~~

## A. Containerised environment

- [Dockerfile](../infrastructure/Dockerfile): a two-stage Python image which builds dependency wheels and installs them offline into the final stage; executes as a non-root user, with Python bytecode writes disabled.
- [Compose extension](../infrastructure/compose.tools.yml): adds a stateless tool service to the root Compose definition. It **reuses** the existing PostgreSQL service and waits on its health check; it does not publish new ports.
- [Bootstrap](../infrastructure/bootstrap.sh): starts the disposable database, builds the image and seeds an **invented** six-company catalogue.
- [Verification](../infrastructure/verify.sh): validates readiness, exact fixture row count, current-name matching and historical-alias matching; returns failure on violated assumptions.

The original multi-stage build compiled a React/Vite dashboard and copied the assets into a Python API container. The showcase does not add a fictional dashboard or claim to deploy the original frontend.

~~~bash
bash infrastructure/bootstrap.sh
bash infrastructure/verify.sh
~~~

Repeat the bootstrap to demonstrate idempotent synthetic seeding. Use only a disposable database: the seed command intentionally replaces demonstration catalogue data. The tools image connects via Compose's private service name rather than localhost. Docker and a functioning Compose v2 installation are required for this example.

## B. Archived database partitions and S3

The historical code existed in a 2025 Python CLI which selected old PostgreSQL partitions, ran pg_dump, uploaded dumps using boto3, inspected remote metadata and could then detach and drop source partitions. It was later removed from the active application. This adaptation improves *safety and verifiability* without claiming execution against a live AWS account.

- [partition_archive.py](../src/osint_research/cloud_archival/partition_archive.py) is the only substantial source adaptation.
- Discovery queries real PostgreSQL inheritance metadata, not name-matching arbitrary tables. A well-formed monthly partition must be older than the explicit cutoff, and the optional live CLI requires one exact partition.
- pg_dump runs as a subprocess with **argv, not shell interpolation**. The database password is forwarded by environment variable rather than embedded in process arguments. Identifiers are strictly validated.
- `pg_dump` writes to a unique, private **same-directory temporary file**, not the final archive path. Before publication, the code checks the custom-format `PGDMP` header, the 64 MiB cap, and a SHA-256 digest. It then creates the final filename **atomically without overwriting** any existing export. A failed export deletes its own incomplete staging file; an abandoned partial file from an interrupted process cannot block a new attempt.
- The completed local file's SHA-256 checksum forms part of a repeatable S3 object key. Server-side encryption is requested via SSE-S3 (AES256).
- The upload is not considered verified until the object's reported size and checksum metadata match **and its downloaded bytes hash to the original SHA-256**. This demonstration deliberately limits verified objects to **64 MiB**, avoiding unexpectedly large readback costs.
- A failed export, upload or verification returns no successful receipt. Failed exports discard only their own incomplete staging file. If S3 uploading or verification fails **after local publication**, the completed local dump is retained for inspection and explicit retry: `archive_partition(..., reuse_existing=True)` rechecks its header, size, and SHA-256 before uploading and verifying the remote object again. The default remains fail-closed when a final filename already exists; symlinks and malformed local dumps cannot be reused.
- **Integrity is not restorable-backup proof:** the five-byte `PGDMP` header and hashes do not replace a PostgreSQL restore test, and there is no signed local provenance manifest. Only explicitly reuse output from a trusted directory. The example CLI uses a temporary directory, so persistent local retry is currently supported through the Python function rather than across CLI invocations.
- The original PostgreSQL partition is **never detached or deleted**, including after successful verification. Unverified S3 objects may remain and require separate cleanup.
- Genuine destructive retention would require an independent restore test, explicit approval, backups, stronger transaction/recovery orchestration and operational policy. None of this is claimed as implemented here.

**Glacier distinction:** Because the sample verifies uploaded bytes immediately, it uses normally readable S3 objects. Transitioning verified archives to Glacier would be a *separate lifecycle operation*, not part of this demonstration.

## C. Reproduce offline (default)

~~~bash
python -m pip install -e '.[dev]'
python scripts/demo_archive.py --dry-run
python -m pytest -q tests/cloud_archival tests/infrastructure
~~~

Offline dry run lists invented partitions and evaluates eligibility without opening a database connection, invoking pg_dump or calling AWS. Tests use a fake subprocess and a fake S3 client, covering malformed partition names, SQL parameterisation, password handling, cutoff dates, deterministic keys, interrupted exports, atomic no-overwrite publication, retry from a completed local dump, checksum corruption and upload failures.

**Optional live upload is deliberately explicit.** This should only be attempted on a dedicated test PostgreSQL instance containing safe data and a disposable S3 bucket:

~~~bash
python -m pip install -e '.[postgres,aws]'
export DATABASE_URL='postgresql://demo-user:...@test-host/test-database'
python scripts/demo_archive.py --live-upload --confirm-live-upload \
  --schema ingest --parent fact_event --partition fact_event_2022_01 \
  --bucket your-test-bucket
~~~

The live path verifies that the named partition is a genuine PostgreSQL child older than the cutoff. It writes exactly one dump to S3 and verifies it, then leaves the source untouched. It is **not** scheduled automation or a retention daemon; AWS IAM permissions and ordinary AWS credential-provider configuration are prerequisites. Never put secrets in the repository.

## Evidence boundaries

| Competency | Claim supported | Claim not supported |
| --- | --- | --- |
| Docker / Compose | Multi-stage builds, non-root runtime, database health dependency | Live cloud deployment or production uptime |
| Bash | Guarded orchestration, readiness, data verification, idempotent synthetic setup | Production deployment orchestration |
| AWS S3 | Historical boto3 archival implementation and safer mocked adaptation | Confirmed historical S3 uploads, Glacier cost savings |
| AWS EC2 | Earlier architectural planning and user-reported experience | Independent proof of an actual EC2 deployment |
| Operational assurance | Deterministic offline failure-path tests and optional real DB example | Full restore rehearsals or destructive retention |

**CV wording supported by published code:** "Developed multi-stage Docker builds and automated database setup and validation using Bash; implemented a PostgreSQL partition archival pipeline using Python/boto3 with data-integrity verification." Describe a historical EC2 deployment separately when its actual deployment records are available.
