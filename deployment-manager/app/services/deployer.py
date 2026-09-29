"""AWS provisioning for the demo ECS environment.

All boto3 calls are deliberately made in worker threads: the deployment API is
async, while boto3 is a blocking client.  The resource names are stable so a
second deployment reuses the existing infrastructure.
"""

import asyncio
import base64
import json
import os
from typing import Any

try:
    from botocore.exceptions import ClientError
except ImportError:  # Keep MOCK_ECS usable in lightweight local environments.
    class ClientError(Exception):
        pass


COMPONENTS = ("ui", "deployment-manager", "container-manager", "sidecar-1",
              "sidecar-2", "agent-1", "agent-2")
SERVICES = ("ui", "deployment-manager", "container-manager", "sidecar-1", "sidecar-2")
TRANSIENT = {"Throttling", "ThrottlingException", "RequestLimitExceeded",
             "ServiceUnavailable", "InternalError", "TooManyRequestsException"}


class AwsDeployer:
    states = ["INITIALIZING", "CREATING_ECR", "CREATING_IAM",
              "CREATING_ECS_CLUSTER", "CREATING_EC2_CAPACITY",
              "CREATING_CAPACITY_PROVIDER", "REGISTERING_TASK_DEFINITIONS",
              "CREATING_SERVICES", "WAITING_FOR_SERVICES", "READY"]

    def __init__(self):
        self.region = ""
        self.names: dict[str, str] = {}
        self.clients: dict[str, Any] = {}

    async def _call(self, client: Any, operation: str, **kwargs: Any) -> dict:
        """Call boto3 with bounded retries for AWS transient failures."""
        for attempt in range(5):
            try:
                return await asyncio.to_thread(getattr(client, operation), **kwargs)
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code not in TRANSIENT or attempt == 4:
                    raise
                await asyncio.sleep(2 ** attempt)

    @staticmethod
    def _env_list(name: str) -> list[str]:
        return [v.strip() for v in os.getenv(name, "").split(",") if v.strip()]

    def _required(self, record: dict) -> tuple[str, list[str], list[str], dict[str, str]]:
        region = record.get("region") or os.getenv("AWS_REGION")
        vpc = record.get("vpc_id") or os.getenv("VPC_ID")
        private = record.get("private_subnet_ids") or self._env_list("PRIVATE_SUBNET_IDS")
        public = record.get("public_subnet_ids") or self._env_list("PUBLIC_SUBNET_IDS")
        images = dict(record.get("image_uris") or {})
        for component in COMPONENTS:
            images.setdefault(component, os.getenv(f"IMAGE_URI_{component.upper().replace('-', '_')}", ""))
        missing = []
        if not region:
            missing.append("region/AWS_REGION")
        if not vpc:
            missing.append("vpc_id/VPC_ID")
        if len(private) < 1:
            missing.append("private_subnet_ids/PRIVATE_SUBNET_IDS")
        if len(public) < 1:
            missing.append("public_subnet_ids/PUBLIC_SUBNET_IDS")
        missing.extend(f"image_uris.{c}" for c in COMPONENTS if not images.get(c))
        if missing:
            raise ValueError("Missing required deployment inputs: " + ", ".join(missing))
        return region, private, public, images

    def _tags(self, record: dict, component: str | None = None) -> list[dict[str, str]]:
        tags = {"Project": "mastro-agent-pool", "Environment": record["environment"],
                "ManagedBy": "deployment-manager", "DeploymentId": record["id"]}
        if component:
            tags["Component"] = component
        return [{"Key": k, "Value": v} for k, v in tags.items()]

    def _ecs_tags(self, record: dict, component: str | None = None) -> list[dict[str, str]]:
        tags = {"Project": "mastro-agent-pool", "Environment": record["environment"],
                "ManagedBy": "deployment-manager", "DeploymentId": record["id"]}
        if component:
            tags["Component"] = component
        return [{"key": k, "value": v} for k, v in tags.items()]

    async def run(self, record: dict) -> None:
        if os.getenv("MOCK_ECS", "true").lower() == "true":
            record.update(state="READY", always_on_services=list(SERVICES),
                          on_demand_agents=["agent-1", "agent-2"])
            return
        self.region, private, public, images = self._required(record)
        record.update(region=self.region, vpc_id=record.get("vpc_id") or os.getenv("VPC_ID"),
                      private_subnet_ids=private, public_subnet_ids=public, image_uris=images)
        import boto3
        self.clients = {n: boto3.client(n, region_name=self.region)
                        for n in ("ecs", "ecr", "iam", "ec2", "ssm",
                                  "autoscaling", "servicediscovery", "elbv2")}
        suffix = f"{record['environment']}-{record['id'][:8]}"
        self.names = {"cluster": f"mastro-{suffix}", "capacity": f"mastro-{suffix}-ec2",
                      "namespace": f"mastro-{suffix}.local", "asg": f"mastro-{suffix}",
                      "lt": f"mastro-{suffix}", "vpc": record["vpc_id"] or os.getenv("VPC_ID")}
        record["state"] = "CREATING_ECR"
        repos = await self._ecr(record)
        record["state"] = "CREATING_IAM"
        roles = await self._iam(record)
        record["state"] = "CREATING_ECS_CLUSTER"
        cluster = await self._cluster(record)
        record["cluster_arn"] = cluster
        record["state"] = "CREATING_EC2_CAPACITY"
        network = await self._network(record, private, public)
        record.update(network)
        await self._capacity(record, private, roles)
        record["state"] = "CREATING_CAPACITY_PROVIDER"
        await self._capacity_provider(record, cluster)
        record["state"] = "REGISTERING_TASK_DEFINITIONS"
        task_defs = await self._task_definitions(record, roles, images, repos, network)
        record["task_definitions"] = task_defs
        record["state"] = "CREATING_SERVICES"
        await self._services(record, cluster, task_defs, network)
        record.update(always_on_services=list(SERVICES), on_demand_agents=["agent-1", "agent-2"])
        record["state"] = "WAITING_FOR_SERVICES"
        await self._wait_for_capacity_and_services(cluster, record)
        record["state"] = "READY"

    async def _wait_for_capacity_and_services(self, cluster: str, record: dict) -> None:
        ecs = self.clients["ecs"]
        deadline = asyncio.get_running_loop().time() + int(os.getenv("ECS_READY_TIMEOUT", "900"))
        while asyncio.get_running_loop().time() < deadline:
            instances = (await self._call(
                ecs, "list_container_instances", cluster=cluster)).get("containerInstanceArns", [])
            if instances:
                services = (await self._call(
                    ecs, "describe_services", cluster=cluster,
                    services=[f"mastro-{name}" for name in SERVICES]))["services"]
                if (len(services) == len(SERVICES)
                        and all(service["runningCount"] >= service["desiredCount"]
                                for service in services)):
                    return
            await asyncio.sleep(15)
        cluster_info = (await self._call(
            ecs, "describe_clusters", clusters=[cluster]))["clusters"][0]
        raise RuntimeError(
            f"ECS capacity did not become ready: "
            f"{cluster_info.get('registeredContainerInstancesCount', 0)} container instances "
            f"registered; services are still pending")

    async def _ecr(self, record: dict) -> dict[str, str]:
        ecr = self.clients["ecr"]; result = {}
        for component in COMPONENTS:
            name = f"mastro-agent-pool-{component}"
            try:
                response = await self._call(ecr, "create_repository", repositoryName=name,
                                            tags=self._tags(record, component))
            except ClientError as exc:
                if exc.response["Error"]["Code"] != "RepositoryAlreadyExistsException":
                    raise
                response = await self._call(ecr, "describe_repositories", repositoryNames=[name])
            result[component] = response["repositories"][0]["repositoryUri"]
        return result

    async def _role(self, record: dict, name: str, policy: dict) -> str:
        iam = self.clients["iam"]
        try:
            response = await self._call(iam, "create_role", RoleName=name,
                AssumeRolePolicyDocument=json.dumps(policy), Tags=self._tags(record))
            arn = response["Role"]["Arn"]
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "EntityAlreadyExists":
                raise
            arn = (await self._call(iam, "get_role", RoleName=name))["Role"]["Arn"]
        return arn

    async def _iam(self, record: dict) -> dict[str, str]:
        suffix = record["id"][:8]
        trust_ec2 = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow",
            "Principal": {"Service": "ec2.amazonaws.com"}, "Action": "sts:AssumeRole"}]}
        trust_ecs = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow",
            "Principal": {"Service": "ecs-tasks.amazonaws.com"}, "Action": "sts:AssumeRole"}]}
        roles = {}
        for key, trust in (("instance", trust_ec2), ("execution", trust_ecs),
                           ("container-manager", trust_ecs), ("agent", trust_ecs),
                           ("sidecar", trust_ecs)):
            roles[key] = await self._role(record, f"mastro-{key}-{suffix}", trust)
        iam = self.clients["iam"]
        managed = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
        await self._call(iam, "attach_role_policy", RoleName=f"mastro-execution-{suffix}",
                         PolicyArn=managed)
        instance_policy = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow",
            "Action": ["ecs:*", "ecr:GetAuthorizationToken", "ecr:BatchGetImage",
                       "ecr:GetDownloadUrlForLayer", "logs:*"], "Resource": "*"}]}
        for key in ("instance", "container-manager", "agent", "sidecar"):
            await self._call(iam, "put_role_policy", RoleName=f"mastro-{key}-{suffix}",
                             PolicyName="mastro-runtime", PolicyDocument=json.dumps(instance_policy))
        return roles

    async def _cluster(self, record: dict) -> str:
        ecs = self.clients["ecs"]; name = self.names["cluster"]
        try:
            response = await self._call(ecs, "create_cluster", clusterName=name,
                tags=self._ecs_tags(record))
            return response["cluster"]["clusterArn"]
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ClusterAlreadyExistsException":
                raise
            return (await self._call(ecs, "describe_clusters", clusters=[name]))["clusters"][0]["clusterArn"]

    async def _network(self, record: dict, private: list[str], public: list[str]) -> dict:
        ec2 = self.clients["ec2"]; vpc = self.names["vpc"]
        try:
            alb = (await self._call(ec2, "create_security_group", GroupName=f"mastro-alb-{record['id'][:8]}",
                Description="Mastro ALB", VpcId=vpc, TagSpecifications=[{"ResourceType": "security-group",
                "Tags": self._tags(record, "alb")}]))["GroupId"]
            app = (await self._call(ec2, "create_security_group", GroupName=f"mastro-app-{record['id'][:8]}",
                Description="Mastro ECS tasks", VpcId=vpc, TagSpecifications=[{"ResourceType": "security-group",
                "Tags": self._tags(record, "ecs")}]))["GroupId"]
            instance = (await self._call(ec2, "create_security_group", GroupName=f"mastro-instance-{record['id'][:8]}",
                Description="Mastro ECS container instances", VpcId=vpc, TagSpecifications=[{"ResourceType": "security-group",
                "Tags": self._tags(record, "ec2")}]))["GroupId"]
            await self._call(ec2, "authorize_security_group_ingress", GroupId=alb, IpPermissions=[
                {"IpProtocol": "tcp", "FromPort": 80, "ToPort": 80, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}])
            await self._call(ec2, "authorize_security_group_ingress", GroupId=app, IpPermissions=[
                {"IpProtocol": "tcp", "FromPort": 8000, "ToPort": 8000, "UserIdGroupPairs": [{"GroupId": alb}]}])
            await self._call(ec2, "authorize_security_group_ingress", GroupId=app, IpPermissions=[
                {"IpProtocol": "tcp", "FromPort": 8000, "ToPort": 8000,
                 "UserIdGroupPairs": [{"GroupId": app}]}])
            await self._call(ec2, "authorize_security_group_ingress", GroupId=instance, IpPermissions=[
                {"IpProtocol": "tcp", "FromPort": 32768, "ToPort": 65535,
                 "UserIdGroupPairs": [{"GroupId": app}]}])
        except ClientError as exc:
            if exc.response["Error"]["Code"] not in ("InvalidGroup.Duplicate",):
                raise
            groups = (await self._call(ec2, "describe_security_groups", Filters=[
                {"Name": "vpc-id", "Values": [vpc]}, {"Name": "tag:Project", "Values": ["mastro-agent-pool"]}]))["SecurityGroups"]
            alb = next(g["GroupId"] for g in groups if any(t["Value"] == "alb" for t in g.get("Tags", []) if t["Key"] == "Component"))
            app = next(g["GroupId"] for g in groups if any(t["Value"] == "ecs" for t in g.get("Tags", []) if t["Key"] == "Component"))
            instance = next(g["GroupId"] for g in groups if any(t["Value"] == "ec2" for t in g.get("Tags", []) if t["Key"] == "Component"))
        sd = self.clients["servicediscovery"]
        namespaces = (await self._call(sd, "list_namespaces"))["Namespaces"]
        match = next((n for n in namespaces if n.get("Name") == self.names["namespace"]), None)
        if match:
            ns_id = match["Id"]
        else:
            try:
                namespace = (await self._call(
                    sd, "create_private_dns_namespace", Name=self.names["namespace"],
                    Vpc=vpc, Tags=self._tags(record)))["OperationId"]
                record["cloud_map_operation_id"] = namespace
                for _ in range(60):
                    namespaces = (await self._call(sd, "list_namespaces"))["Namespaces"]
                    match = next(
                        (item for item in namespaces if item.get("Name") == self.names["namespace"]),
                        None,
                    )
                    if match:
                        ns_id = match["Id"]
                        break
                    await asyncio.sleep(5)
                else:
                    operation = await self._call(sd, "get_operation", OperationId=namespace)
                    raise RuntimeError(
                        f"Cloud Map namespace creation timed out; operation_id={namespace}; "
                        f"status={operation['Operation'].get('Status')}; "
                        f"error={operation['Operation'].get('ErrorMessage', 'none')}"
                    )
            except ClientError as exc:
                if exc.response["Error"]["Code"] != "NamespaceAlreadyExists":
                    raise
                namespaces = (await self._call(sd, "list_namespaces"))["Namespaces"]
                match = next((n for n in namespaces if n.get("Name") == self.names["namespace"]), None)
                if not match:
                    raise RuntimeError(f"Cloud Map namespace {self.names['namespace']} was not found")
                ns_id = match["Id"]
        discovery = {}
        for component in COMPONENTS:
            try:
                service = await self._call(sd, "create_service", Name=component, NamespaceId=ns_id,
                                           DnsConfig={"RoutingPolicy": "MULTIVALUE",
                                                      "DnsRecords": [{"Type": "A", "TTL": 10}]},
                                           Tags=self._tags(record, component))
                discovery[component] = service["Service"]["Arn"]
            except ClientError as exc:
                if exc.response["Error"]["Code"] != "ServiceAlreadyExists":
                    raise
                existing = (await self._call(sd, "list_services",
                    Filters=[{"Name": "NAMESPACE_ID", "Values": [ns_id]}]))["Services"]
                discovery[component] = next(s["Arn"] for s in existing if s["Name"] == component)
        return {"alb_security_group": alb, "task_security_group": app, "instance_security_group": instance,
                "private_subnets": private, "public_subnets": public,
                "namespace_id": ns_id, "discovery_services": discovery}

    async def _capacity(self, record: dict, private: list[str], roles: dict) -> None:
        ec2, asg = self.clients["ec2"], self.clients["autoscaling"]
        ami = (await self._call(self.clients["ssm"], "get_parameter",
            Name="/aws/service/ecs/optimized-ami/amazon-linux-2023/recommended/image_id"))["Parameter"]["Value"]
        profile_name = f"mastro-profile-{record['id'][:8]}"
        try:
            await self._call(self.clients["iam"], "create_instance_profile",
                             InstanceProfileName=profile_name, Tags=self._tags(record))
            await self._call(self.clients["iam"], "add_role_to_instance_profile",
                             InstanceProfileName=profile_name, RoleName=f"mastro-instance-{record['id'][:8]}")
        except ClientError as exc:
            if exc.response["Error"]["Code"] not in ("EntityAlreadyExists", "LimitExceeded"):
                raise
        # IAM resources are eventually consistent.  EC2 rejects the launch
        # template/ASG when the profile cannot yet be resolved by name.
        profile = self.clients["iam"]
        for attempt in range(12):
            try:
                details = await self._call(profile, "get_instance_profile",
                                           InstanceProfileName=profile_name)
                if details["InstanceProfile"].get("Roles"):
                    break
            except ClientError as exc:
                if exc.response["Error"]["Code"] != "NoSuchEntity":
                    raise
            if attempt == 11:
                raise RuntimeError(f"IAM instance profile {profile_name} was not ready")
            await asyncio.sleep(5)
        profile_arn = details["InstanceProfile"]["Arn"]
        await asyncio.sleep(30)
        data = base64.b64encode(f"#!/bin/bash\necho ECS_CLUSTER={self.names['cluster']} >> /etc/ecs/ecs.config\n".encode()).decode()
        try:
            launch_template = await self._call(ec2, "create_launch_template", LaunchTemplateName=self.names["lt"],
                LaunchTemplateData={"ImageId": ami, "InstanceType": os.getenv("ECS_INSTANCE_TYPE", "t3.micro"),
                    "IamInstanceProfile": {"Arn": profile_arn}, "UserData": data,
                    "SecurityGroupIds": [record["instance_security_group"]]},
                TagSpecifications=[{"ResourceType": "launch-template", "Tags": self._tags(record)}])
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "InvalidLaunchTemplateName.AlreadyExistsException":
                raise
            launch_template = await self._call(
                ec2, "describe_launch_templates", LaunchTemplateNames=[self.names["lt"]])
        launch_template_id = launch_template["LaunchTemplate"]["LaunchTemplateId"] \
            if "LaunchTemplate" in launch_template else launch_template["LaunchTemplates"][0]["LaunchTemplateId"]
        try:
            await self._call(asg, "create_auto_scaling_group", AutoScalingGroupName=self.names["asg"],
                LaunchTemplate={"LaunchTemplateId": launch_template_id, "Version": "$Latest"},
                MinSize=int(os.getenv("ECS_MIN_INSTANCES", "1")), MaxSize=int(os.getenv("ECS_MAX_INSTANCES", "5")),
                DesiredCapacity=int(os.getenv("ECS_DESIRED_INSTANCES", "1")), VPCZoneIdentifier=",".join(private),
                NewInstancesProtectedFromScaleIn=True, Tags=[{**t, "PropagateAtLaunch": True} for t in self._tags(record)])
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "AlreadyExists":
                raise

    async def _capacity_provider(self, record: dict, cluster: str) -> None:
        ecs = self.clients["ecs"]; cp = self.names["capacity"]
        try:
            await self._call(ecs, "create_capacity_provider", name=cp,
                autoScalingGroupProvider={"autoScalingGroupArn": (await self._call(
                    self.clients["autoscaling"], "describe_auto_scaling_groups",
                    AutoScalingGroupNames=[self.names["asg"]]))["AutoScalingGroups"][0]["AutoScalingGroupARN"],
                    "managedScaling": {"status": "ENABLED", "targetCapacity": 75,
                                       "minimumScalingStepSize": 1, "maximumScalingStepSize": 2},
                    "managedTerminationProtection": "ENABLED", "managedDraining": "ENABLED",
                }, tags=self._ecs_tags(record))
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ClientException":
                raise
        await self._call(ecs, "put_cluster_capacity_providers", cluster=cluster,
                         capacityProviders=[cp], defaultCapacityProviderStrategy=[{"capacityProvider": cp, "weight": 1}])

    async def _task_definitions(self, record: dict, roles: dict, images: dict[str, str],
                                repos: dict[str, str], network: dict) -> dict[str, str]:
        ecs = self.clients["ecs"]; result = {}
        execution = roles["execution"]; suffix = record["id"][:8]
        namespace = self.names["namespace"]
        order = ("agent-1", "agent-2", "ui", "deployment-manager",
                 "sidecar-1", "sidecar-2", "container-manager")
        for component in order:
            family = f"mastro-{component}-{suffix}"
            environment = [
                {"name": "AWS_REGION", "value": self.region},
                {"name": "SERVICE_DISCOVERY_NAMESPACE", "value": namespace},
            ]
            if component == "container-manager":
                environment.extend([
                    {"name": "MOCK_ECS", "value": "false"},
                    {"name": "ECS_CLUSTER", "value": self.names["cluster"]},
                    {"name": "ECS_CAPACITY_PROVIDER", "value": self.names["capacity"]},
                    {"name": "ECS_PRIVATE_SUBNETS", "value": ",".join(network["private_subnets"])},
                    {"name": "ECS_TASK_SECURITY_GROUP", "value": network["task_security_group"]},
                    {"name": "AGENT_1_TASK_DEFINITION", "value": result["agent-1"]},
                    {"name": "AGENT_2_TASK_DEFINITION", "value": result["agent-2"]},
                    {"name": "CONTAINER_MANAGER_URL", "value": f"http://container-manager.{namespace}:8000"},
                ])
            elif component == "agent-1":
                environment.extend([
                    {"name": "AGENT_TYPE", "value": "agent-1"},
                    {"name": "CONTAINER_MANAGER_URL", "value": f"http://container-manager.{namespace}:8000"},
                    {"name": "SIDECAR_2_URL", "value": f"http://sidecar-2.{namespace}:8000"},
                ])
            elif component == "agent-2":
                environment.extend([
                    {"name": "AGENT_TYPE", "value": "agent-2"},
                    {"name": "CONTAINER_MANAGER_URL", "value": f"http://container-manager.{namespace}:8000"},
                ])
            elif component == "ui":
                environment.append({"name": "SIDECAR_1_URL", "value": f"http://sidecar-1.{namespace}:8000"})
            elif component in ("sidecar-1", "sidecar-2"):
                environment.append({"name": "CONTAINER_MANAGER_URL", "value": f"http://container-manager.{namespace}:8000"})
            container = {"name": component, "image": images[component], "essential": True,
                         "portMappings": [{"containerPort": 8000, "protocol": "tcp"}],
                         "environment": environment}
            response = await self._call(ecs, "register_task_definition", family=family,
                requiresCompatibilities=["EC2"], networkMode="awsvpc", cpu="256", memory="512",
                executionRoleArn=execution, taskRoleArn=roles["agent" if component.startswith("agent") else
                ("sidecar" if component.startswith("sidecar") else "container-manager")],
                containerDefinitions=[container], tags=self._ecs_tags(record, component))
            result[component] = response["taskDefinition"]["taskDefinitionArn"]
        return result

    async def _services(self, record: dict, cluster: str, task_defs: dict[str, str], network: dict) -> None:
        ecs = self.clients["ecs"]
        for component in SERVICES:
            kwargs = {"cluster": cluster, "serviceName": f"mastro-{component}",
                      "taskDefinition": task_defs[component], "desiredCount": 1,
                      "capacityProviderStrategy": [{"capacityProvider": self.names["capacity"], "weight": 1}],
                      "deploymentConfiguration": {"maximumPercent": 200, "minimumHealthyPercent": 50},
                      "networkConfiguration": {"awsvpcConfiguration": {"subnets": network["private_subnets"],
                          "securityGroups": [network["task_security_group"]], "assignPublicIp": "DISABLED"}},
                      "serviceRegistries": [{"registryArn": network["discovery_services"][component]}],
                      "tags": self._ecs_tags(record, component)}
            if component == "ui":
                alb = await self._alb(record, network)
                kwargs["loadBalancers"] = [{"targetGroupArn": alb, "containerName": component,
                                            "containerPort": 8000}]
            try:
                await self._call(ecs, "create_service", **kwargs)
            except ClientError as exc:
                if exc.response["Error"]["Code"] != "InvalidParameterException" or "already exists" not in str(exc).lower():
                    raise

    async def _alb(self, record: dict, network: dict) -> str:
        elb = self.clients["elbv2"]
        lb = await self._call(elb, "create_load_balancer", Name=f"mastro-{record['id'][:8]}",
                              Subnets=network["public_subnets"], SecurityGroups=[network["alb_security_group"]],
                              Scheme="internet-facing", Type="application", Tags=self._tags(record, "ui"))
        arn = lb["LoadBalancers"][0]["LoadBalancerArn"]
        tg = await self._call(elb, "create_target_group", Name=f"mastro-ui-{record['id'][:8]}",
                              Protocol="HTTP", Port=8000, VpcId=self.names["vpc"], TargetType="ip",
                              HealthCheckPath="/health", Tags=self._tags(record, "ui"))
        await self._call(elb, "create_listener", LoadBalancerArn=arn, Protocol="HTTP", Port=80,
                         DefaultActions=[{"Type": "forward", "TargetGroupArn": tg["TargetGroups"][0]["TargetGroupArn"]}])
        record["load_balancer_dns"] = lb["LoadBalancers"][0]["DNSName"]
        return tg["TargetGroups"][0]["TargetGroupArn"]
