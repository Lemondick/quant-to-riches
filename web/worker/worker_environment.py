"""Keep database and server key storage credentials out of compute processes."""


def worker_environment(environment):
    excluded = {"QUANT_SECRET_ROOT", "QUANT_INIT_ADMIN_PASSWORD"}
    return {
        key: value
        for key, value in environment.items()
        if key.upper() not in excluded
        and not key.upper().startswith(("PG", "QUANT_PG", "QUANT_TEST_PG", "CONNECTIONSTRINGS__"))
    }
