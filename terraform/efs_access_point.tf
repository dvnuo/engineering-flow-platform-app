# The EFS access point of the app host's volume.
#
# A snippet for the Terraform configuration that already owns the EFS file
# system (engineering-flow-platform-portal/terraform). Set
# var.efp_efs_file_system_id to that file system, or replace the variable with
# aws_efs_file_system.portal.id when you paste this next to it.
#
# What an access point does, and why the host has isolation modes: every file
# operation through it is performed as posix_user below, whatever user the
# process in the pod is. Apps run as different OS users, yet on this volume
# they are all the same user. The host therefore never relies on file
# ownership here (docs/OPERATIONS.md, "Choosing the isolation mode").

variable "efp_efs_file_system_id" {
  description = "The EFS file system the app host's volume lives on."
  type        = string
}

variable "efp_cluster_name" {
  description = "Used in the name tag of the access point."
  type        = string
  default     = "efp"
}

resource "aws_efs_access_point" "apps" {
  file_system_id = var.efp_efs_file_system_id

  # The same user as the Portal's and the assistants' access points.
  posix_user {
    uid = 1000
    gid = 1000
  }

  # A directory of its own: the host must not see the Portal's database or
  # the assistants' workspaces, and they must not see the apps.
  root_directory {
    path = "/apps"

    creation_info {
      owner_uid   = 1000
      owner_gid   = 1000
      permissions = "700"
    }
  }

  tags = {
    Name = "${var.efp_cluster_name}-apps-ap"
  }
}

output "efp_apps_access_point_id" {
  description = "EFS access point of the app host."
  value       = aws_efs_access_point.apps.id
}

# What k8s/storage.yaml wants as spec.csi.volumeHandle.
output "efp_apps_volume_handle" {
  description = "volumeHandle for the PersistentVolume of the app host."
  value       = "${var.efp_efs_file_system_id}::${aws_efs_access_point.apps.id}"
}

# Back the volume up (releases can be redeployed from GitHub; the apps' data
# cannot). With AWS Backup, add the file system to a plan, for example:
#
# resource "aws_backup_selection" "efp_apps" {
#   name         = "efp-apps"
#   plan_id      = aws_backup_plan.daily.id
#   iam_role_arn = aws_iam_role.backup.arn
#   resources    = ["arn:aws:elasticfilesystem:${var.region}:${var.account_id}:file-system/${var.efp_efs_file_system_id}"]
# }
