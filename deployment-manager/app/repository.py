class DeploymentRepository:
    def __init__(self): self.items = {}
    def save(self, item): self.items[item["id"]] = item
    def get(self, key): return self.items.get(key)
    def all(self): return list(self.items.values())
