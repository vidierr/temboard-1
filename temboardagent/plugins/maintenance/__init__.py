from datetime import datetime
from pickle import loads as unpickle

from temboardagent.errors import UserError
from temboardagent.routing import RouteSet
from temboardagent.toolkit import taskmanager
from temboardagent.tools import validate_parameters
from temboardagent.types import T_OBJECTNAME

from . import functions


routes = RouteSet(prefix=b'/maintenance')


@routes.get(b'', check_key=True)
def get_instance(http_context, app):
    with app.postgres.connect() as conn:
        instance = next(functions.get_instance(conn))

    with app.postgres.connect() as conn:
        rows = functions.get_databases(conn)

    databases = []
    for database in rows:
        # we need to connect with a different database
        dbname = database['datname']
        with functions.get_postgres(app.config, dbname).connect() \
                as conn:
            database.update(**functions.get_database(conn))
        databases.append(database)

    return {'instance': instance, 'databases': databases}


T_DATABASE_NAME = T_OBJECTNAME
T_SCHEMA_NAME = T_OBJECTNAME
T_TABLE_NAME = T_OBJECTNAME
T_INDEX_NAME = T_OBJECTNAME
T_VACUUM_MODE = b'(((^|,)(full|freeze|analyze))+$)'
T_TIMESTAMP_UTC = b'(^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$)'
T_OPERATION_ID = b'(^[0-9a-f]{8}$)'


@routes.get(b'/%s' % (T_DATABASE_NAME), check_key=True)
def get_database(http_context, app):
    dbname = http_context['urlvars'][0]
    with functions.get_postgres(app.config, dbname).connect() as conn:
        database = functions.get_database_size(conn)
        schemas = functions.get_schemas(conn)
    return dict(database, **{'schemas': schemas})


@routes.get(b'/%s/schema/%s' % (T_DATABASE_NAME, T_SCHEMA_NAME),
            check_key=True)
def get_schema(http_context, app):
    dbname = http_context['urlvars'][0]
    schema = http_context['urlvars'][1]
    with functions.get_postgres(app.config, dbname).connect() \
            as conn:
        tables = functions.get_tables(conn, schema)
        indexes = functions.get_schema_indexes(conn, schema)
        schema = functions.get_schema(conn, schema)
    return dict(dict(tables, **indexes), **schema)


@routes.get(b'/%s/schema/%s/table/%s' % (T_DATABASE_NAME, T_SCHEMA_NAME,
                                         T_TABLE_NAME),
            check_key=True)
def get_table(http_context, app):
    dbname = http_context['urlvars'][0]
    schema = http_context['urlvars'][1]
    table = http_context['urlvars'][2]

    with functions.get_postgres(app.config, dbname).connect() \
            as conn:
        ret = functions.get_table(conn, schema, table)
        ret.update(**functions.get_table_indexes(conn, schema, table))
        return ret


@routes.post(b'/%s/schema/%s/table/%s/vacuum' % (T_DATABASE_NAME,
                                                 T_SCHEMA_NAME,
                                                 T_TABLE_NAME))
def post_vacuum(http_context, app):
    dbname = http_context['urlvars'][0]
    schema = http_context['urlvars'][1]
    table = http_context['urlvars'][2]
    # Parameters format validation
    post = http_context['post']
    if 'datetime' in post:
        validate_parameters(post, [
            ('datetime', T_TIMESTAMP_UTC, False),
        ])
    dt = post.get('datetime', datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"))
    if 'mode' in post:
        validate_parameters(post, [
            ('mode', T_VACUUM_MODE, False),
        ])
    mode = post.get('mode', '')

    with functions.get_postgres(app.config, dbname).connect() as conn:
        return functions.schedule_vacuum(conn, dbname, schema, table, mode,
                                         dt, app)


@routes.get(b'/%s/schema/%s/table/%s/vacuum/scheduled' % (T_DATABASE_NAME,
                                                          T_SCHEMA_NAME,
                                                          T_TABLE_NAME),
            check_key=True)
def scheduled_vacuum_table(http_context, app):
    dbname = http_context['urlvars'][0]
    schema = http_context['urlvars'][1]
    table = http_context['urlvars'][2]
    return functions.list_scheduled_vacuum(app, dbname=dbname, schema=schema,
                                           table=table)


@routes.delete(b'/vacuum/' + T_OPERATION_ID)
def delete_vacuum(http_context, app):
    vacuum_id = http_context['urlvars'][0]
    return functions.cancel_scheduled_operation(vacuum_id, app)


@routes.get(b'/vacuum/scheduled', check_key=True)
def scheduled_vacuum(http_context, app):
    return functions.list_scheduled_vacuum(app)


@taskmanager.worker(pool_size=10)
def vacuum_worker(config, dbname, schema, table, mode):
    config = unpickle(config)

    with functions.get_postgres(config, dbname).connect() \
            as conn:
        return functions.vacuum(conn, dbname, schema, table, mode)


@routes.post(b'/%s/schema/%s/table/%s/analyze' % (T_DATABASE_NAME,
                                                  T_SCHEMA_NAME,
                                                  T_TABLE_NAME))
def post_analyze(http_context, app):
    dbname = http_context['urlvars'][0]
    schema = http_context['urlvars'][1]
    table = http_context['urlvars'][2]
    # Parameters format validation
    post = http_context['post']
    if 'datetime' in post:
        validate_parameters(post, [
            ('datetime', T_TIMESTAMP_UTC, False),
        ])
    dt = post.get('datetime', datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"))

    with functions.get_postgres(app.config, dbname).connect() as conn:
        return functions.schedule_analyze(conn, dbname, schema, table, dt, app)


@routes.get(b'/%s/schema/%s/table/%s/analyze/scheduled' % (T_DATABASE_NAME,
                                                           T_SCHEMA_NAME,
                                                           T_TABLE_NAME),
            check_key=True)
def scheduled_analyze_table(http_context, app):
    dbname = http_context['urlvars'][0]
    schema = http_context['urlvars'][1]
    table = http_context['urlvars'][2]
    return functions.list_scheduled_analyze(app, dbname=dbname, schema=schema,
                                            table=table)


@routes.delete(b'/analyze/' + T_OPERATION_ID)
def delete_analyze(http_context, app):
    analyze_id = http_context['urlvars'][0]
    return functions.cancel_scheduled_operation(analyze_id, app)


@routes.get(b'/analyze/scheduled', check_key=True)
def scheduled_analyze(http_context, app):
    return functions.list_scheduled_analyze(app)


@taskmanager.worker(pool_size=10)
def analyze_worker(config, dbname, schema, table):
    config = unpickle(config)

    with functions.get_postgres(config, dbname).connect() \
            as conn:
        return functions.analyze(conn, dbname, schema, table)


@routes.post(b'/%s/schema/%s/index/%s/reindex' % (T_DATABASE_NAME,
                                                  T_SCHEMA_NAME,
                                                  T_INDEX_NAME))
def post_reindex(http_context, app):
    dbname = http_context['urlvars'][0]
    schema = http_context['urlvars'][1]
    index = http_context['urlvars'][2]
    # Parameters format validation
    post = http_context['post']
    if 'datetime' in post:
        validate_parameters(post, [
            ('datetime', T_TIMESTAMP_UTC, False),
        ])
    dt = post.get('datetime', datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"))

    with functions.get_postgres(app.config, dbname).connect() as conn:
        return functions.schedule_reindex(conn, dbname, schema, index, dt, app)


@routes.get(b'/%s/schema/%s/table/%s/reindex/scheduled' %
            (T_DATABASE_NAME, T_SCHEMA_NAME, T_TABLE_NAME),
            check_key=True)
def scheduled_reindex_index(http_context, app):
    dbname = http_context['urlvars'][0]
    schema = http_context['urlvars'][1]
    return functions.list_scheduled_reindex(app, dbname=dbname, schema=schema)


@routes.get(b'/%s/schema/%s/reindex/scheduled' % (T_DATABASE_NAME,
                                                  T_SCHEMA_NAME),
            check_key=True)
def scheduled_reindex_index_(http_context, app):
    dbname = http_context['urlvars'][0]
    schema = http_context['urlvars'][1]
    return functions.list_scheduled_reindex(app, dbname=dbname, schema=schema)


@routes.delete(b'/reindex/' + T_OPERATION_ID)
def delete_reindex(http_context, app):
    reindex_id = http_context['urlvars'][0]
    return functions.cancel_scheduled_operation(reindex_id, app)


@routes.get(b'/reindex/scheduled', check_key=True)
def scheduled_reindex(http_context, app):
    return functions.list_scheduled_reindex(app)


@taskmanager.worker(pool_size=10)
def reindex_worker(config, dbname, schema, index):
    config = unpickle(config)

    with functions.get_postgres(config, dbname).connect() as conn:
        return functions.reindex(conn, dbname, schema, index)


class MaintenancePlugin(object):
    PG_MIN_VERSION = 90400

    def __init__(self, app, **kw):
        self.app = app

    def load(self):
        pg_version = self.app.postgres.fetch_version()
        if pg_version < self.PG_MIN_VERSION:
            msg = "%s is incompatible with Postgres below 9.4" % (
                self.__class__.__name__)
            raise UserError(msg)

        self.app.router.add(routes)
        for route in routes:
            print(route)

    def unload(self):
        self.app.router.remove(routes)
