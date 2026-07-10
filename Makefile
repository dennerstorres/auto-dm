.PHONY: unit e2e all

unit:
	INVITE_CODE= pytest --ignore=tests/e2e

e2e:
	docker compose -f docker-compose.e2e.yml up -d --wait
	AUTO_DM_E2E=1 DATABASE_URL=postgresql+asyncpg://auto_dm:auto_dm_e2e@127.0.0.1:35432/auto_dm_e2e REDIS_URL=redis://127.0.0.1:36379/0 pytest tests/e2e -n 2 --maxfail=1; status=$$?; docker compose -f docker-compose.e2e.yml down -v; exit $$status

all: unit e2e
