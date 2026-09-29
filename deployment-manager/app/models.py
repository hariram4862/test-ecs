from pydantic import BaseModel, Field
class DeployRequest(BaseModel):
    environment: str = "demo"
    region: str = "ap-south-1"
    vpc_id: str | None = None
    private_subnet_ids: list[str] = Field(default_factory=list)
    public_subnet_ids: list[str] = Field(default_factory=list)
    image_uris: dict[str, str] = Field(default_factory=dict)
