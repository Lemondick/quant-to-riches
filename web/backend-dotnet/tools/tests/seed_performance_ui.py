"""Synthetic fixtures confined to the explicitly named loopback UI test database."""

import argparse
import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

import psycopg2
from argon2 import PasswordHasher

TEST_PASSWORD = "SyntheticPerformancePassword123!"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    # Never accept a production connection string for a fixture-writing tool.
    with (
        psycopg2.connect(
            host="127.0.0.1", port=55439, dbname="quant_perf_ui", user="quant_test"
        ) as connection,
        connection.cursor() as cursor,
    ):
        if arguments.check:
            cursor.execute("SELECT count(*) FROM compute_jobs")
            count = cursor.fetchone()[0]
            cursor.execute("SELECT kind,count(*) FROM compute_jobs GROUP BY kind")
            print(json.dumps({"compute_jobs": count, "kinds": dict(cursor.fetchall())}))
            return
        cursor.execute(
            "SELECT count(*) FROM users WHERE username NOT IN ('perf_ui_alice','perf_ui_bob')"
        )
        if cursor.fetchone()[0]:
            raise RuntimeError("The UI fixture database contains unrelated accounts")
        cursor.execute("DELETE FROM compute_jobs")
        now = datetime.now(UTC)
        hasher = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
        for username, balance in (("perf_ui_alice", "1000"), ("perf_ui_bob", "2000")):
            cursor.execute("SELECT id FROM users WHERE username=%s", [username])
            existing = cursor.fetchone()
            user = existing[0] if existing else uuid4().hex
            if not existing:
                cursor.execute(
                    "INSERT INTO users(id,username,password_hash,role,status,created_at) "
                    "VALUES(%s,%s,%s,'user','active',%s)",
                    [user, username, hasher.hash(TEST_PASSWORD), now],
                )
            cursor.execute(
                "UPDATE users SET role=%s,status='active' WHERE id=%s",
                ["admin" if username.endswith("alice") else "user", user],
            )
            cursor.execute(
                "INSERT INTO "
                "positions(user_id,symbol,asset_type,quantity,average_cost,market_value,"
                "as_of_date,updated_at) "
                "VALUES(%s,'CNY','cash',%s,1,%s,%s,%s) ON CONFLICT(user_id,symbol) DO "
                "UPDATE SET quantity=EXCLUDED.quantity,market_value=EXCLUDED.market_value",
                [user, balance, balance, now.date(), now],
            )
            if username.endswith("alice"):
                cursor.execute(
                    "INSERT INTO instruments(user_id,symbol,asset_type,name,as_of_date) "
                    "VALUES(%s,'510300','fund','Synthetic UI Fund',%s) ON CONFLICT DO NOTHING",
                    [user, now.date()],
                )
                cursor.execute(
                    "INSERT INTO "
                    "positions(user_id,symbol,asset_type,quantity,average_cost,"
                    "market_value,as_of_date,updated_at) "
                    "VALUES(%s,'510300','fund',100,1,125,%s,%s) ON "
                    "CONFLICT(user_id,symbol) DO UPDATE SET "
                    "quantity=100,average_cost=1,market_value=125",
                    [user, now.date(), now],
                )
                preferences = {
                    "watchlist": [
                        {
                            "symbol": "510300",
                            "name": "Synthetic UI Fund",
                            "asset_type": "fund",
                        }
                    ],
                    "read_news_ids": [],
                    "revision": 1,
                    "updated_at": now.isoformat(),
                }
                cursor.execute(
                    "INSERT INTO user_documents(user_id,document_key,revision,payload) "
                    "VALUES(%s,'preferences',1,%s::jsonb) "
                    "ON CONFLICT(user_id,document_key) DO UPDATE SET payload=EXCLUDED.payload",
                    [user, json.dumps(preferences)],
                )

        def snapshot(kind, payload, result):
            raw = json.dumps(payload, separators=(",", ":"))
            digest = hashlib.sha256((kind + ":" + raw).encode()).hexdigest()
            cursor.execute(
                "INSERT INTO "
                "public_compute_snapshots(kind,input_hash,input_json,result_json,"
                "completed_at,expires_at) "
                "VALUES(%s,%s,%s::jsonb,%s::jsonb,%s,%s+interval '60 seconds') "
                "ON CONFLICT(kind,input_hash) DO UPDATE SET "
                "result_json=EXCLUDED.result_json,completed_at=EXCLUDED.completed_at,"
                "expires_at=EXCLUDED.expires_at",
                [kind, digest, raw, json.dumps(result), now, now],
            )

        snapshot(
            "market.snapshot",
            {"section": None},
            {
                "indices": [
                    {
                        "symbol": "000300",
                        "name": "Synthetic UI Index",
                        "asset_type": "index",
                        "price": "100.25",
                        "change_rate": "1.25",
                        "observed_at": now.isoformat(),
                        "source": "synthetic_test_fixture",
                    }
                ],
                "fund_rankings": [],
                "sector_rankings": [],
                "observed_at": now.isoformat(),
                "source": "synthetic_test_fixture",
                "stale": False,
                "unavailable": [],
            },
        )
        snapshot(
            "fund.valuations",
            {"symbols": ["510300"]},
            {
                "items": [
                    {
                        "symbol": "510300",
                        "estimate_nav": "1.25",
                        "previous_nav": "1.20",
                        "change_rate": "4.1667",
                        "observed_at": now.isoformat(),
                        "source": "synthetic_test_fixture",
                        "nav_date": now.date().isoformat(),
                        "valuation_type": "intraday_estimate",
                    }
                ],
                "unavailable": [],
                "estimate_only": True,
            },
        )
        snapshot(
            "market.quotes",
            {"stocks": [], "indices": ["000300"]},
            {
                "items": [
                    {
                        "symbol": "000300",
                        "asset_type": "index",
                        "name": "Synthetic UI Index",
                        "price": "100.25",
                        "change_rate": "1.25",
                        "observed_at": now.isoformat(),
                        "source": "synthetic_test_fixture",
                        "trade_time_verified": True,
                    }
                ],
                "unavailable": [],
                "observed_at": now.isoformat(),
                "source": "synthetic_test_fixture",
                "stale": False,
            },
        )
        snapshot(
            "market.news",
            {"limit": 50},
            {
                "items": [],
                "unavailable": False,
                "observed_at": now.isoformat(),
                "source": "synthetic_test_fixture",
            },
        )
        snapshot(
            "fund.detail",
            {"symbol": "510300"},
            {
                "symbol": "510300",
                "name": "Synthetic UI Fund",
                "fund_type": None,
                "company": None,
                "nav_history": [],
                "unavailable": [],
                "observed_at": now.isoformat(),
                "source": "synthetic_test_fixture",
                "stale": False,
            },
        )
        print("Isolated UI fixtures ready")


if __name__ == "__main__":
    main()
