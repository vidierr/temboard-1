import logging
import os
import subprocess
from getpass import getuser
from glob import iglob
from pathlib import Path
from textwrap import dedent

import pytest
from sh import (
    chown, locale,
)

from .utils import rmtree


logger = logging.getLogger(__name__)


class PostgreSQLVersions(dict):
    # A mapping from major version -> bindir.

    # List of agent supported PostgreSQL versions.
    SUPPORTED_VERSIONS = [
        '14',
        '13',
        '12',
        '11',
        '10',
        '9.6',
        '9.5',
        '9.4',
    ]

    def search_installed_versions(self):
        patterns = [
            '/usr/lib/postgresql/*/bin/initdb',
            '/usr/pgsql-*/bin/initdb',
            '/usr/local/bin/initdb',
            '/usr/bin/initdb',
        ]
        for pattern in patterns:
            for initdb in iglob(pattern):
                res = subprocess.run(
                    [initdb, "--version"], stdout=subprocess.PIPE)
                res.check_returncode()
                out = res.stdout.decode('utf-8').split()
                assert 'initdb' == out[0]
                assert '(PostgreSQL)' == out[1]
                version = out[2]
                if not version.startswith('9.'):
                    version, _ = version.split('.')
                else:
                    version = version[:3]

                bindir = str(Path(initdb).parent)
                if version in self:
                    logger.info(
                        "Found duplicate installation for %s at %s.",
                        version, bindir,
                    )
                elif version in self.SUPPORTED_VERSIONS:
                    logger.info(
                        "Found supported version %s at %s.",
                        version, bindir,
                    )
                    self[version] = bindir
                else:
                    logger.info(
                        "Found unsupported version %s at %s.",
                        version, bindir)

    @property
    def most_recent_version(self):
        sorted_version = sorted(
            self.keys(),
            key=float,
            reverse=True,
        )
        return sorted_version[0]


POSTGRESQL_AVAILABLE_VERSIONS = PostgreSQLVersions()


def find_locale():
    out = locale(a=True)
    for candidate in ('en_US', 'fr_FR'):
        candidate = candidate + '.utf8'
        if candidate in out:
            return candidate
    else:
        raise Exception("Missing en_US.utf8 locale.")


@pytest.fixture(scope='session')
def postgres(agent_env, pguser, sudo_pguser, workdir):
    """
    Initialize a PostgreSQL instance for monitoring by a temBoard agent.

    pgdata is in tests/workdir/var/pgdata. Executes postgres as a subprocess
    of pytest. See pguser fixture for the process and files owner policy.

    Returns pgdata directory object.
    """

    # workdir fixture warranties an empty directory.
    pgdata = workdir / 'var/pgdata'
    logger.info("Creating %s.", pgdata)
    pgdata.mkdir()
    logdir = workdir / 'var/log/postgresql'
    logdir.mkdir()
    socketdir = Path(agent_env['PGHOST'])
    socketdir.mkdir()
    chown("--recursive", pguser, pgdata, logdir, socketdir)

    locale_ = find_locale()

    logger.info("Initializing database at %s.", pgdata)
    pwfile = workdir / 'pwfile'
    pwfile.write_text(agent_env['PGPASSWORD'])
    sudo_pguser.initdb(
            locale=locale_,
            username=agent_env['PGUSER'],
            auth_local="md5",
            pwfile=str(pwfile),
            pgdata=str(pgdata),
        )
    pwfile.unlink()

    auto = pgdata / 'postgresql.auto.conf'
    logger.info("Writing %s.", auto)
    config = auto.read_text()
    auto.write_text(dedent(f"""\
    {config}
    include_dir = 'conf.d'
    """))

    conffile = pgdata / 'conf.d' / 'temboard-tests.conf'
    conffile.parent.mkdir()
    logger.info("Writing %s.", conffile)
    pidfile = workdir / 'run/postgres.pid'
    conffile.write_text(dedent(f"""\
    cluster_name = 'temboard-tests'
    external_pid_file = '{pidfile}'
    log_directory = '{logdir}'
    log_line_prefix = '%t [%p]: user=%u,db=%d,app=%a,client=%h '
    log_lock_waits = on
    logging_collector = on
    port = {agent_env['PGPORT']}
    unix_socket_directories = '{socketdir}'
    """))

    logger.info("Starting instance at %s.", pgdata)
    sudo_pguser.pg_ctl(f"--pgdata={pgdata}", "start")
    sudo_pguser.psql(c='SELECT version();', _env=agent_env)  # pentest

    yield pgdata

    logger.info("Stopping instance at %s.", pgdata)
    sudo_pguser.pg_ctl(f"--pgdata={pgdata}", "--mode=immediate", "stop")
    rmtree(pgdata)


@pytest.fixture(scope='session', autouse=True)
def pgbin(pg_version):
    """
    Inject in PATH PostgreSQL bin directory for chosen version.
    """

    bindir = POSTGRESQL_AVAILABLE_VERSIONS[pg_version]
    logger.info("Using %s.", bindir)
    os.environ['PATH'] = f"{bindir}:{os.environ['PATH']}"
    return bindir


@pytest.fixture(scope='session')
def pguser():
    """
    Determine UNIX user for executing Postgres and agent.
    """
    me = getuser()
    user = 'postgres' if 'root' == me else me
    logger.info("Using UNIX user %s for Postgres and agent.", user)
    return user


@pytest.fixture(scope='session')
def pg_version(request):
    """ Reads chosen PostgreSQL major version. """
    return request.config.getoption("--pg-version")