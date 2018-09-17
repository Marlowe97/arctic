import os
import logging
from multiprocessing.pool import ThreadPool

from .async.async_arctic import ASYNC_ARCTIC

try:
    from lz4.block import compress as lz4_compress, decompress as lz4_decompress
    lz4_compressHC = lambda _str: lz4_compress(_str, mode='high_compression')
except ImportError as e:
    from lz4 import compress as lz4_compress, compressHC as lz4_compressHC, decompress as lz4_decompress


logger = logging.getLogger(__name__)

# switch to parallel LZ4 compress (and potentially other parallel stuff), Default True
ENABLE_PARALLEL = not os.environ.get('DISABLE_PARALLEL')
LZ4_HIGH_COMPRESSION = bool(os.environ.get('LZ4_HIGH_COMPRESSION'))

# Flag to control whether to use separate thread pool (lz4 own pool) or use the common async thread pool
LZ4_USE_ASYNC_POOL = bool(os.environ.get('LZ4_USE_ASYNC_POOL'))
# For a guide on how to tune the following parameters, read:
#     arctic/benchmarks/lz4_tuning/README.txt
# The size of the compression thread pool.
# Rule of thumb: use 2 for non HC (VersionStore/NDarrayStore/PandasStore, and 8 for HC (TickStore).
LZ4_WORKERS = os.environ.get('LZ4_WORKERS', 2)
# The minimum required number of chunks to use parallel compression
LZ4_N_PARALLEL = os.environ.get('LZ4_N_PARALLEL', 16)
# Minimum data size to use parallel compression
LZ4_MINSZ_PARALLEL = os.environ.get('LZ4_MINSZ_PARALLEL', 0.5*1024**2)  # 0.5 MB

# Enable this when you run the benchmark_lz4.py
BENCHMARK_MODE = False

_compress_thread_pool = None


def _init_thread_pool(use_async_pool=None, pool_size=None):
    global _compress_thread_pool, LZ4_USE_ASYNC_POOL, LZ4_WORKERS

    # Always pick up the latest global var values
    use_async_pool = bool(LZ4_USE_ASYNC_POOL if use_async_pool is None else use_async_pool)
    pool_size = int(LZ4_WORKERS if pool_size is None else pool_size)

    if _compress_thread_pool is not None and not LZ4_USE_ASYNC_POOL:
        try:
            _compress_thread_pool.close()
            _compress_thread_pool.join()
        except Exception as e:
            logging.error("Failed to shut down the local compression thread pool.")

    if use_async_pool:
        logging.info("Using the common async pool, omitting LZ4 pool size.")
        _compress_thread_pool = ASYNC_ARCTIC
        LZ4_USE_ASYNC_POOL = use_async_pool
    else:
        logging.info("Using separate LZ4 thread pool with size {}".format(LZ4_WORKERS))
        _compress_thread_pool = ThreadPool(LZ4_WORKERS)
        LZ4_WORKERS = pool_size


def _get_compression_pool():
    if _compress_thread_pool is None:
        _init_thread_pool()
    return _compress_thread_pool._workers_pool if LZ4_USE_ASYNC_POOL else _compress_thread_pool


def enable_parallel_lz4(mode):
    """
    Set the global multithread compression mode

    Parameters
    ----------
        mode: `bool`
            True: Use parallel compression. False: Use sequential compression
    """
    global ENABLE_PARALLEL
    ENABLE_PARALLEL = bool(mode)
    logger.info("Setting parallelization mode to {}".format("multithread" if mode else "singlethread"))


def set_compression_pool_size(pool_size):
    """
    Set the size of the compression workers thread pool.
    If the pool is already created, it waits until all jobs are finished, and then proceeds with setting the new size.

    Parameters
    ----------
        pool_size : `int`
            The size of the pool (must be a positive integer)

    Returns
    -------
    `None`
    """
    if LZ4_USE_ASYNC_POOL:
        logging.warn("Can't set the compression pool size when using the common async pool")
        return

    pool_size = int(pool_size)
    if pool_size < 1:
        raise ValueError("The compression thread pool size cannot be of size {}".format(pool_size))

    if LZ4_WORKERS == pool_size:
        return

    _init_thread_pool(pool_size=pool_size)


def set_use_async_pool(use_async_pool):
    """
    Configure compression to use either custom thread pool or the common async pool

    Parameters
    ----------
    use_async_pool : `bool`
        The boolean flag which control the use or not of the common async pool

    Returns
    -------

    """
    use_async_pool = bool(use_async_pool)
    if LZ4_USE_ASYNC_POOL is use_async_pool:
        return

    _init_thread_pool(use_async_pool=use_async_pool)


def compress_array(str_list, withHC=LZ4_HIGH_COMPRESSION):
    """
    Compress an array of strings

    Parameters
    ----------
        str_list: `list[str]`
            The input list of strings which need to be compressed.
        withHC: `bool`
            This flag controls whether lz4HC will be used.

    Returns
    -------
    `list[str`
    The list of the compressed strings.
    """
    global _compress_thread_pool

    if not str_list:
        return str_list

    do_compress = lz4_compressHC if withHC else lz4_compress

    use_parallel = ENABLE_PARALLEL and withHC or \
                   len(str_list) > LZ4_N_PARALLEL and len(str_list[0]) > LZ4_MINSZ_PARALLEL

    if BENCHMARK_MODE or use_parallel:
        return _get_compression_pool().map(do_compress, str_list)

    return [do_compress(s) for s in str_list]
    

def compress(_str):
    """
    Compress a string

    By default LZ4 mode is standard in interactive mode,
    and high compresion in applications/scripts
    """
    return lz4_compress(_str)


def compressHC(_str):
    """
    HC compression
    """
    return lz4_compressHC(_str)


def compressHC_array(str_list):
    """
    HC compression
    """
    return compress_array(str_list, withHC=True)


def decompress(_str):
    """
    Decompress a string
    """
    return lz4_decompress(_str)


def decompress_array(str_list):
    """
    Decompress a list of strings
    """
    global _compress_thread_pool

    if not str_list:
        return str_list

    if not ENABLE_PARALLEL or len(str_list) <= LZ4_N_PARALLEL:
        return [lz4_decompress(chunk) for chunk in str_list]

    return _get_compression_pool().map(lz4_decompress, str_list)
