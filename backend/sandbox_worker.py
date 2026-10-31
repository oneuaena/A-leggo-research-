"""Experimental Linux calculation worker. Never invoke on public requests until verified."""
import ctypes
import ctypes.util
import errno
import json
import math
import os
import resource
import sys
import traceback
import fractions
import decimal
import statistics
import itertools
import functools
import collections

LIBC = ctypes.CDLL(None, use_errno=True)
LIBSECCOMP = ctypes.CDLL(ctypes.util.find_library('seccomp') or 'libseccomp.so.2', use_errno=True)


class RulesetAttr(ctypes.Structure):
    _fields_ = [('handled_access_fs', ctypes.c_uint64)]


class PathBeneathAttr(ctypes.Structure):
    _fields_ = [('allowed_access', ctypes.c_uint64), ('parent_fd', ctypes.c_int32)]


def checked(result, name):
    if result < 0:
        raise OSError(ctypes.get_errno(), name)
    return result


def confine_filesystem(workspace):
    abi = checked(LIBC.syscall(444, 0, 0, 1), 'landlock ABI')
    if abi < 3:
        raise RuntimeError('Landlock ABI 3 required')
    rights = (1 << 15) - 1
    ruleset = RulesetAttr(rights)
    fd = checked(LIBC.syscall(444, ctypes.byref(ruleset), ctypes.sizeof(ruleset), 0), 'landlock ruleset')
    path_fd = os.open(workspace, os.O_PATH | os.O_CLOEXEC)
    try:
        rule = PathBeneathAttr(rights, path_fd)
        checked(LIBC.syscall(445, fd, 1, ctypes.byref(rule), 0), 'landlock path')
        checked(LIBC.prctl(38, 1, 0, 0, 0), 'no_new_privs')
        checked(LIBC.syscall(446, fd, 0), 'landlock restrict')
    finally:
        os.close(path_fd)
        os.close(fd)


def confine_syscalls():
    sc = LIBSECCOMP
    sc.seccomp_init.argtypes = [ctypes.c_uint32]
    sc.seccomp_init.restype = ctypes.c_void_p
    sc.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    sc.seccomp_syscall_resolve_name.restype = ctypes.c_int
    sc.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    sc.seccomp_rule_add.restype = ctypes.c_int
    sc.seccomp_load.argtypes = [ctypes.c_void_p]
    sc.seccomp_load.restype = ctypes.c_int
    sc.seccomp_release.argtypes = [ctypes.c_void_p]
    context = sc.seccomp_init(0x00050000 | errno.EPERM)
    if not context:
        raise RuntimeError('seccomp init failed')
    names = ('read write close fstat lseek mmap mprotect munmap brk rt_sigaction '
             'rt_sigprocmask rt_sigreturn ioctl pread64 readv writev sched_yield '
             'mremap madvise dup dup2 dup3 nanosleep getpid gettid getrandom '
             'clock_gettime clock_nanosleep clock_getres gettimeofday time futex '
             'getrusage fcntl getcwd openat getdents64 unlinkat renameat renameat2 '
             'mkdirat rmdir unlink rename mkdir fsync fdatasync ftruncate truncate '
             'umask exit exit_group').split()
    try:
        for name in names:
            nr = sc.seccomp_syscall_resolve_name(name.encode())
            if nr >= 0 and sc.seccomp_rule_add(context, 0x7fff0000, nr, 0) != 0:
                raise RuntimeError('seccomp rule failed: ' + name)
        if sc.seccomp_load(context) != 0:
            raise RuntimeError('seccomp load failed')
    finally:
        sc.seccomp_release(context)


def run():
    request = json.loads(sys.stdin.readline(65537))
    code = request['code']
    workspace = os.path.realpath(request['workspace'])
    if not isinstance(code, str) or len(code) > 16000 or workspace != os.getcwd():
        raise ValueError('invalid input')
    if len(os.listdir('/proc/self/task')) != 1:
        raise RuntimeError('worker must be single threaded')
    os.closerange(3, 1024)
    resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (29, 30))
    resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
    confine_filesystem(workspace)
    confine_syscalls()
    try:
        exec(compile(code, '<calculation>', 'exec'), {'__name__': '__main__'})
    except BaseException:
        traceback.print_exc(limit=5)
        raise SystemExit(1)


if __name__ == '__main__':
    try:
        run()
    except BaseException as exc:
        print('计算未完成：' + type(exc).__name__, file=sys.stderr)
        raise SystemExit(2)
