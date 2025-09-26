import os


def check_env_variables(env_vars):
    for env_name in env_vars:
        if not os.environ.get(env_name):
            raise ValueError(f"Env Variable: {env_name} is missing!")