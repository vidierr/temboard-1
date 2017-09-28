import logging.config
from logging.handlers import SysLogHandler

try:
    import configparser
except ImportError:
    import ConfigParser as configparser

import os.path
import json
import re
from temboardagent.errors import ConfigurationError
from .utils import DotDict
from .pluginsmgmt import load_plugins_configurations


logger = logging.getLogger(__name__)


LOG_METHODS = {
    'file': {
        '()': 'logging.FileHandler',
        'mode': 'a',
        'formatter': 'dated_syslog',
    },
    'syslog': {
        '()': 'logging.handlers.SysLogHandler',
        'formatter': 'syslog',
    },
    'stderr': {
        '()': 'logging.StreamHandler',
        'formatter': 'minimal',
    },
}

LOG_FACILITIES = SysLogHandler.facility_names
LOG_LEVELS = logging._levelNames.values()
LOG_FORMAT = '[%(name)-32.32s %(levelname)5.5s] %(message)s'


def generate_logging_config(config):
    LOG_METHODS['file']['filename'] = config.logging['destination']
    facility = SysLogHandler.facility_names[config.logging['facility']]
    LOG_METHODS['syslog']['facility'] = facility
    LOG_METHODS['syslog']['address'] = config.logging['destination']
    syslog_fmt = (
        "temboard-agent[%(process)d]: [%(name)s] %(levelname)s: %(message)s"
    )

    logging_config = {
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {
            'minimal': {'format': LOG_FORMAT},
            'syslog': {'format': syslog_fmt},
            'dated_syslog': {'format': '%(asctime)s ' + syslog_fmt},
        },
        'handlers': {
            'configured': LOG_METHODS[config.logging['method']]
        },
        'root': {
            'level': 'INFO',
            # Avoid instanciate all handlers, especially syslog which open
            # /dev/log
            'handlers': ['configured'],
        },
        'loggers': {
            'temboardagent': {
                'level': config.logging['level'],
            },
            'temboard-agent': {
                'level': config.logging['level'],
            },
        },
    }
    return logging_config


def setup_logging(config):
    logging_config = generate_logging_config(config)
    logging.config.dictConfig(logging_config)


class BaseConfiguration(configparser.RawConfigParser):
    """
    Common configuration parser.
    """
    def __init__(self, configfile, *args, **kwargs):
        configparser.RawConfigParser.__init__(self, *args, **kwargs)
        self.configfile = os.path.realpath(configfile)
        self.confdir = os.path.dirname(self.configfile)

        # Default configuration values
        self.temboard = {
            'port': 2345,
            'address': '0.0.0.0',
            'users': '/etc/temboard-agent/users',
            'ssl_cert_file': None,
            'ssl_key_file': None,
            'plugins': [
                "monitoring",
                "dashboard",
                "pgconf",
                "administration",
                "activity"
            ],
            'home': os.environ.get('HOME', '/var/lib/temboard-agent'),
            'hostname': None,
            'key': None
        }
        self.logging = {
            'method': 'syslog',
            'facility': 'local0',
            'destination': '/dev/log',
            'level': 'DEBUG'
        }
        self.postgresql = {
            'host': '/var/run/postgresql',
            'user': 'postgres',
            'port': 5432,
            'password': None,
            'dbname': 'postgres',
            'pg_config': '/usr/bin/pg_config',
            'instance': 'main'
        }
        try:
            with open(self.configfile) as fd:
                self.readfp(fd)
        except IOError:
            raise ConfigurationError("Configuration file %s can't be opened."
                                     % (self.configfile))
        except configparser.MissingSectionHeaderError:
            raise ConfigurationError(
                    "Configuration file does not contain section headers.")

    def check_section(self, section):
        if not self.has_section(section):
            raise ConfigurationError(
                    "Section '%s' not found in configuration file %s"
                    % (section, self.configfile))

    def abspath(self, path):
        if path.startswith('/'):
            return path
        else:
            return os.path.realpath('/'.join([self.confdir, path]))

    def getfile(self, section, name):
        path = self.abspath(self.get(section, name))
        try:
            with open(path) as fd:
                fd.read()
        except Exception as e:
            logger.warn("Failed to open %s: %s", path, e)
            raise ConfigurationError("%s file can't be opened." % (path,))
        return path


class Configuration(BaseConfiguration):
    """
    Customized configuration parser.
    """
    def __init__(self, configfile, *args, **kwargs):
        BaseConfiguration.__init__(self, configfile, *args, **kwargs)
        self.plugins = {}
        # Test if 'temboard' section exists.
        self.check_section('temboard')

        try:
            if not (self.getint('temboard', 'port') >= 0
                    and self.getint('temboard', 'port') <= 65535):
                raise ValueError()
            self.temboard['port'] = self.getint('temboard', 'port')
        except ValueError:
            raise ConfigurationError("'port' option must be an integer "
                                     "[0-65535] in %s." % (self.configfile))
        except configparser.NoOptionError:
            pass
        try:
            if not re.match(r'(?:[3-9]\d?|2(?:5[0-5]|[0-4]?\d)?|1\d{0,2}|\d)'
                            '(\.(?:[3-9]\d?|2(?:5[0-5]|[0-4]?\d)?|1\d{0,2}|\d'
                            ')){3}$', self.get('temboard', 'address')):
                raise ValueError()
            self.temboard['address'] = self.get('temboard', 'address')
        except ValueError:
            raise ConfigurationError("'address' option must be a valid IPv4 "
                                     "address in %s." % (self.configfile))
        except configparser.NoOptionError:
            pass

        try:
            self.temboard['users'] = self.getfile('temboard', 'users')
        except configparser.NoOptionError:
            pass

        try:
            plugins = json.loads(self.get('temboard', 'plugins'))
            if not type(plugins) == list:
                raise ValueError()
            for plugin in plugins:
                if not re.match('^[a-zA-Z0-9]+$', str(plugin)):
                    raise ValueError
            self.temboard['plugins'] = plugins
        except configparser.NoOptionError:
            pass
        except ValueError:
            raise ConfigurationError("'plugins' option must be a list of "
                                     "string (alphanum only) in %s." % (
                                         self.configfile))
        try:
            self.temboard['key'] = self.get('temboard', 'key')
        except configparser.NoOptionError:
            pass

        try:
            self.temboard['ssl_cert_file'] = (
                self.getfile('temboard', 'ssl_cert_file'))
        except configparser.NoOptionError:
            pass

        try:
            self.temboard['ssl_key_file'] = (
                self.getfile('temboard', 'ssl_key_file'))
        except configparser.NoOptionError:
            pass

        try:
            home = self.get('temboard', 'home')
            if not os.access(home, os.W_OK):
                raise Exception()
            self.temboard['home'] = self.get('temboard', 'home')
        except configparser.NoOptionError:
            pass
        except Exception:
            raise ConfigurationError("Home directory %s not writable."
                                     % (self.get('temboard', 'home')))

        try:
            hostname = self.get('temboard', 'hostname')
            self.temboard['hostname'] = hostname
        except configparser.NoOptionError:
            pass

        # Test if 'logging' section exists.
        self.check_section('logging')
        try:
            if not self.get('logging', 'method') in LOG_METHODS:
                raise ValueError()
            self.logging['method'] = self.get('logging', 'method')
        except ValueError:
            raise ConfigurationError("Invalid 'method' option in 'logging' "
                                     "section in %s." % (self.configfile))
        except configparser.NoOptionError:
            pass
        try:
            if not self.get('logging', 'facility') in LOG_FACILITIES:
                raise ValueError()
            self.logging['facility'] = self.get('logging', 'facility')
        except ValueError:
            raise ConfigurationError("Invalid 'facility' option in 'logging' "
                                     "section in %s." % (self.configfile))
        except configparser.NoOptionError:
            pass
        try:
            self.logging['destination'] = self.get('logging', 'destination')
        except configparser.NoOptionError:
            pass
        try:
            if not self.get('logging', 'level') in LOG_LEVELS:
                raise ValueError()
            self.logging['level'] = self.get('logging', 'level')
        except ValueError:
            raise ConfigurationError("Invalid 'level' option in 'logging' "
                                     "section in %s." % (self.configfile))
        except configparser.NoOptionError:
            pass

        # Test if 'postgresql' section exists.
        self.check_section('postgresql')
        try:
            from os import path
            if not path.exists(self.get('postgresql', 'host')):
                raise ValueError()
            self.postgresql['host'] = self.get('postgresql', 'host')
        except ValueError:
            raise ConfigurationError("'host' option must be a valid directory"
                                     " path containing PostgreSQL's local unix"
                                     " socket in %s." % (self.configfile))
        except configparser.NoOptionError:
            pass

        try:
            self.postgresql['user'] = self.get('postgresql', 'user')
        except configparser.NoOptionError:
            pass

        try:
            if not (self.getint('postgresql', 'port') >= 0
                    and self.getint('postgresql', 'port') <= 65535):
                raise ValueError()
            self.postgresql['port'] = self.getint('postgresql', 'port')
        except ValueError:
            raise ConfigurationError("'port' option must be an integer "
                                     "[0-65535] in 'postgresql' section in %s."
                                     % (self.configfile))
        except configparser.NoOptionError:
            pass

        try:
            self.postgresql['password'] = self.get('postgresql', 'password')
        except configparser.NoOptionError:
            pass

        try:
            self.postgresql['dbname'] = self.get('postgresql', 'dbname')
        except configparser.NoOptionError:
            pass
        try:
            self.postgresql['instance'] = self.get('postgresql', 'instance')
        except configparser.NoOptionError:
            pass


class PluginConfiguration(configparser.RawConfigParser):
    """
    Customized configuration parser for plugins.
    """
    def __init__(self, configfile, *args, **kwargs):
        configparser.RawConfigParser.__init__(self, *args, **kwargs)
        self.configfile = configfile
        self.confdir = os.path.dirname(self.configfile)

        try:
            with open(self.configfile) as fd:
                self.readfp(fd)
        except IOError:
            raise ConfigurationError("Configuration file %s can't be opened."
                                     % (self.configfile))
        except configparser.MissingSectionHeaderError:
            raise ConfigurationError("Configuration file does not contain "
                                     "section headers.")

    def check_section(self, section):
        if not self.has_section(section):
            raise ConfigurationError("Section '%s' not found in configuration "
                                     "file %s" % (section, self.configfile))

    def abspath(self, path):
        if path.startswith('/'):
            return path
        else:
            return os.path.realpath('/'.join([self.confdir, path]))

    def getfile(self, section, name):
        path = self.abspath(self.get(section, name))
        try:
            with open(path) as fd:
                fd.read()
        except Exception as e:
            logger.warn("Failed to open %s: %s", path, e)
            raise ConfigurationError("%s file can't be opened." % (path,))
        return path


class LazyConfiguration(BaseConfiguration):
    """
    Customized configuration parser.
    """
    def __init__(self, configfile, *args, **kwargs):
        BaseConfiguration.__init__(self, configfile, *args, **kwargs)
        # Test if 'temboard' section exists.
        self.check_section('temboard')
        for k, v in self.temboard.iteritems():
            try:
                self.temboard[k] = self.get('temboard', k)
            except configparser.NoOptionError:
                pass
        # Test if 'logging' section exists.
        self.check_section('logging')
        for k, v in self.logging.iteritems():
            try:
                self.logging[k] = self.get('logging', k)
            except configparser.NoOptionError:
                pass
        # Test if 'postgresql' section exists.
        self.check_section('postgresql')
        for k, v in self.logging.iteritems():
            try:
                self.postgresql[k] = self.get('postgresql', k)
            except configparser.NoOptionError:
                pass


# Here begin the new API
#
# The purpose of the new API is to merge args, file, environment and defaults
# safely, even when reloading.
#
# The API must be very simple, IoC-free. Implementation must be highly testable
# and tested.


class OptionSpec(object):
    # Hold known name and default of an option.
    #
    # An option *must* be specified to follow the principle of *validated your
    # inputs*.
    #
    # Defining defaults here is agnostic from origin : argparse, environ,
    # ConfigParser, etc. The origin of configuration must not take care of
    # default nor validation.

    def __init__(self, section, name, default=None):
        self.section = section
        self.name = name
        self.default = default

    def __repr__(self):
        return '<OptionSpec %s>' % (self,)

    def __str__(self):
        return '%s_%s' % (self.section, self.name)

    def __eq__(self, other):
        return str(self) == str(other)

    def __hash__(self):
        return hash(str(self))


def load_configuration(specs, args):
    # Main entry point to load configuration.
    #
    # specs is a list or a flat dict of OptionSpecs
    #
    # argparser should **not** manage defaults. Use argparse.SUPPRESS as
    # argument_default to store only user defined arguments. MergeConfiguration
    # merge defaults after file and environ are loaded. Defaults from argparse
    # are considered user input and overrides file and environ.
    #
    # configfile **must** be store in dest `temboard_configfile` in args.
    #
    # Origin order: args > environ > file > defaults

    config = MergedConfiguration(specs)
    config.load(args)
    return config


class Value(object):
    # Hold an option value and its origin
    def __init__(self, name, value, origin):
        self.name = name
        self.value = value
        self.origin = origin

    def __repr__(self):
        return '<%(name)s=%(value)r %(origin)s>' % self.__dict__


def iter_args_values(args):
    # Walk args from argparse and yield values.
    for k, v in args.__dict__.items():
        yield Value(k, v, 'args')


def iter_defaults(specs):
    # Walk specs flat dict and yield default values.
    for spec in specs.values():
        yield Value(str(spec), spec.default, 'defaults')


class MergedConfiguration(DotDict):
    # Merge and holds configuration from args, files and more
    #
    # Origin order: args > environ > file > defaults

    def __init__(self, specs=None):
        # Spec is a flat dict of OptionSpec.
        specs = specs or {}
        specs = specs if isinstance(specs, dict) else {s: s for s in specs}

        # Add required configfile option
        spec = OptionSpec(
            'temboard', 'configfile',
            default='/etc/temboard-agent/temboard-agent.conf',
        )
        specs.setdefault(spec, spec)

        DotDict.__init__(self)
        self.__dict__['specs'] = specs
        self.loaded = False

    def add_values(self, values):
        # Merge **missing* values. No override.
        for value in values:
            spec = self.specs[value.name]
            section = self.setdefault(spec.section, {})
            if spec.name in section:
                # Skip already defined values
                continue
            section[spec.name] = value.value

    def load(self, args):
        # Origins are loaded in order. First wins (except file due to legacy).

        self.add_values(iter_args_values(args))

        # Loading default for configfile *before* loading file.
        self.setdefault('temboard', {})
        self.temboard.setdefault(
            'configfile', self.specs['temboard_configfile'].default,
        )

        logger.debug('Loading %s.', self.temboard.configfile)
        fileconfig = Configuration(self.temboard.configfile)
        self.load_file(fileconfig)
        self.plugins = load_plugins_configurations(self)
        self.add_values(iter_defaults(self.specs))
        self.loaded = True

    def load_file(self, fileconfig):
        # This is a glue with legacy file-only configuration loading.
        #
        # File is loaded and validated in a single step using legacy code.
        # Values from file overrides previous defined values (including
        # args...).
        #
        # This glue will be dropped once validated is extended to all origin of
        # configuration.

        for name in {'temboard', 'logging', 'postgresql'}:
            values = getattr(fileconfig, name, {})
            section = self.setdefault(name, {})
            for k, v in values.items():
                section[k] = v

        # Compat with fileconfig
        self.configfile = fileconfig.configfile
        self.confdir = fileconfig.confdir

    def reload(self):
        # Reread file config.

        assert self.loaded, "Can't reload unloaded configuration."
        old_plugins = self.temboard.plugins

        logger.debug('Loading %s.', self.temboard.configfile)
        fileconfig = Configuration(self.temboard.configfile)
        self.load_file(fileconfig)
        # Prevent any change on plugins list.
        self.temboard.plugins = old_plugins
        # Now reload plugins configurations
        self.plugins = load_plugins_configurations(self)
        return self

    def setup_logging(self):
        # Just to save one import for code reloading config.
        setup_logging(self)
