import asyncio, sys, httpx
async def main():
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    async with httpx.AsyncClient(timeout=60) as client:
        results = await asyncio.gather(*(client.post("http://localhost:8080/workflow/start", json={"i": i}) for i in range(count)), return_exceptions=True)
    print({"requested": count, "completed": sum(isinstance(r, httpx.Response) and r.is_success for r in results)})
if __name__ == "__main__": asyncio.run(main())
