import cffi
import os
import re
import sys
import warnings
import rpy2.situation
from rpy2.rinterface_lib import ffi_proxy


DEFINES = ('RPY2_RLEN_LONG', 'RPY2_RLEN_SHORT', 'OSNAME_NT')
ifdef_pat_template = (r'(^#ifdef {name}\n)'
                      r'((?:.*\n)*)'
                      r'(#else  /\* {name} \*/\n)'
                      r'((?:.*\n)*)'
                      r'(#endif  /\* {name} \*/\n)')
define_pat = re.compile('^#define +([^ ]+) +([^ ]+) *$')
cffi_source_pat = re.compile(
    '^/[*] cffi_source-begin [*]/.+?/[*] cffi_source-end [*]/',
    flags=re.MULTILINE | re.DOTALL)


def define_rlen_kind(ffibuilder, definitions):
    if ffibuilder.sizeof('size_t') > 4:
        # The following was defined in the first cffi port,
        # and they in the C-extension version. They should
        # be ported to the header.
        # LONG_VECTOR_SUPPORT = True
        # R_XLEN_T_MAX = 4503599627370496
        # R_SHORT_LEN_MAX = 2147483647
        definitions['RPY2_RLEN_LONG'] = True
    else:
        definitions['RPY2_RLEN_SHORT'] = True


def define_osname(definitions):
    if os.name == 'nt':
        definitions['OSNAME_NT'] = True


def parse(iterrows, rownum, definitions, until=None):
    res = []
    for row_i, row in enumerate(iter(iterrows), rownum):
        if until and row.startswith(until):
            break
        m = define_pat.match(row)
        if m:
            alias, value = m.groups()
            definitions[alias] = value.strip()
            continue
        for k, v in definitions.items():
            if isinstance(v, str):
                row = row.replace(k, v)
        res.append(row)
    return res


def create_cdef(definitions, header_filename):
    cdef = []
    with open(
            os.path.join(
                os.path.dirname(os.path.realpath(__file__)),
                'rinterface_lib',
                header_filename)
    ) as fh:
        iterrows = iter(fh)
        cdef.extend(parse(iterrows, 0, definitions))
    res = ''.join(cdef)
    for name in DEFINES:
        pat = ifdef_pat_template.format(name=name)
        if name in definitions:
            replace_idx = 2
        else:
            replace_idx = 4
        m = re.search(pat, res, flags=re.MULTILINE)
        while m:
            beg, end = m.span()
            replace_str = m.group(replace_idx)
            res = ''.join((res[:beg], replace_str, res[end:]))
            m = re.search(pat, res, flags=re.MULTILINE)
    return res


def read_source(src_filename):
    with open(
            os.path.join(
                os.path.dirname(os.path.realpath(__file__)),
                'rinterface_lib',
                src_filename)
    ) as fh:
        cdef = fh.read()
    return cdef


def createbuilder_abi():
    ffibuilder = cffi.FFI()
    cdef = []
    definitions = {}
    define_rlen_kind(ffibuilder, definitions)
    define_osname(definitions)
    cdef = create_cdef(definitions, 'R_API.h')
    ffibuilder.set_source('_rinterface_cffi_abi', None)
    ffibuilder.cdef(cdef)
    return ffibuilder


def createbuilder_api():
    header_filename = 'R_API.h'
    ffibuilder = cffi.FFI()
    definitions = {}
    define_rlen_kind(ffibuilder, definitions)
    define_osname(definitions)
    cdef = create_cdef(definitions, header_filename)
    eventloop_h = read_source('R_API_eventloop.h')
    eventloop_c = read_source('R_API_eventloop.c')
    r_home = rpy2.situation.get_r_home()
    if r_home is None:
        sys.exit('Error: rpy2 in API mode cannot be built without R in '
                 'the PATH or R_HOME defined. Correct this or force '
                 'ABI mode-only by defining the environment variable '
                 'RPY2_CFFI_MODE=ABI')
    c_ext = rpy2.situation.CExtensionOptions()
    c_ext.add_lib(
        *rpy2.situation.get_r_flags(r_home, '--ldflags')
    )
    c_ext.add_include(
        *rpy2.situation.get_r_flags(r_home, '--cppflags')
    )
    define_macros = [('RPY2_RLEN_LONG'
                      if 'RPY2_RLEN_LONG' in definitions
                      else 'RPY2_RLEN_SHORT', True)]

    ffibuilder.set_source(
        '_rinterface_cffi_api',
        eventloop_c,
        libraries=c_ext.libraries,
        library_dirs=c_ext.library_dirs,
        # If we were using the R headers, we would use
        # include_dirs=c_ext.include_dirs.
        include_dirs=['rpy2/rinterface_lib/'],
        define_macros=define_macros,
        extra_compile_args=c_ext.extra_compile_args,
        extra_link_args=c_ext.extra_link_args)

    callback_defns_api = os.linesep.join(
        x.extern_python_def
        for x in [ffi_proxy._capsule_finalizer_def,
                  ffi_proxy._evaluate_in_r_def,
                  ffi_proxy._consoleflush_def,
                  ffi_proxy._consoleread_def,
                  ffi_proxy._consolereset_def,
                  ffi_proxy._consolewrite_def,
                  ffi_proxy._consolewrite_ex_def,
                  ffi_proxy._showmessage_def,
                  ffi_proxy._choosefile_def,
                  ffi_proxy._cleanup_def,
                  ffi_proxy._showfiles_def,
                  ffi_proxy._processevents_def,
                  ffi_proxy._busy_def,
                  ffi_proxy._callback_def,
                  ffi_proxy._yesnocancel_def,
                  ffi_proxy._parsevector_wrap_def,
                  ffi_proxy._handler_def])

    cdef = (create_cdef(definitions, header_filename) +
            callback_defns_api)
    ffibuilder.cdef(cdef)
    ffibuilder.cdef(cffi_source_pat.sub('', eventloop_h))
    ffibuilder.cdef('void rpy2_runHandlers(InputHandler *handlers);')
    return ffibuilder


# This sort of redundant with setup.py defining cffi_modules,
# but at least both use rpy2.situation.get_ffi_mode().
cffi_mode = rpy2.situation.get_cffi_mode()

if cffi_mode in (rpy2.situation.CFFI_MODE.ABI,
                 rpy2.situation.CFFI_MODE.BOTH):
    ffibuilder_abi = createbuilder_abi()
elif cffi_mode == rpy2.situation.CFFI_MODE.ANY:
    try:
        ffibuilder_abi = createbuilder_abi()
    except Exception as e:
        warnings.warn(str(e))
        ffibuilder_abi = None

if cffi_mode in (rpy2.situation.CFFI_MODE.API,
                 rpy2.situation.CFFI_MODE.BOTH):
    ffibuilder_api = createbuilder_api()
elif cffi_mode == rpy2.situation.CFFI_MODE.ANY:
    try:
        ffibuilder_api = createbuilder_api()
    except Exception as e:
        warnings.warn(str(e))
        ffibuilder_api = None

if __name__ == '__main__':

    if cffi_mode in (rpy2.situation.CFFI_MODE.ABI,
                     rpy2.situation.CFFI_MODE.BOTH):
        ffibuilder_abi.compile(verbose=True)
    elif cffi_mode == rpy2.situation.CFFI_MODE.ANY:
        try:
            ffibuilder_api.compile(verbose=True)
        except Exception as e:
            warnings.warn(str(e))

    if cffi_mode in (rpy2.situation.CFFI_MODE.API,
                     rpy2.situation.CFFI_MODE.BOTH):
        ffibuilder_api.compile(verbose=True)
    elif cffi_mode == rpy2.situation.CFFI_MODE.ANY:
        try:
            ffibuilder_api.compile(verbose=True)
        except Exception as e:
            warnings.warn(str(e))
