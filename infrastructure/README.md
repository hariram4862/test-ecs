# AWS infrastructure

The deployment manager owns AWS provisioning with boto3. This directory intentionally
contains no Terraform files or state. Production provisioning should extend
`deployment-manager/app/services/deployer.py` with ECR, IAM, launch template,
Auto Scaling Group, ECS capacity provider, service discovery, security groups,
and ALB creation using the configured VPC and subnet IDs.
