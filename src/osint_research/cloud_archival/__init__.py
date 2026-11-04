"""Safe historical cloud-archival source adaptation."""
from .partition_archive import ArchiveReceipt, PartitionRef, archive_partition, discover_eligible_partitions, eligible, plan_archive
__all__ = ["ArchiveReceipt","PartitionRef","archive_partition","discover_eligible_partitions","eligible","plan_archive"]
