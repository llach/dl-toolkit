import logging
import os
import socket
import time

import coloredlogs

home = os.environ['HOME']
model_path = os.environ['HOME'] + '/.forkan/models/'
chosen_path = f'{home}/.forkan/chosen-ones/'
dataset_path = os.environ['HOME'] + '/.forkan/datasets/'
figure_path = os.environ['HOME'] + '/.forkan/figures/'
log_file = os.environ['HOME'] + '/.forkan/log.txt'

from logging.config import dictConfig
from forkan.common.utils import create_dir, textbf, textcolor

logging_config = dict(
    version=1,
    formatters={
        'f': {'format':
              '%(asctime)s [%(levelname)-2s] %(name)-4s %(message)s',
              'datefmt': '%H:%M'}
        },
    handlers={
        'h': {'class': 'logging.StreamHandler',
              'formatter': 'f',
              'level': logging.DEBUG}
        },
    root={
        'handlers': ['h'],
        'level': logging.DEBUG,
        },
)

# config for coloredlogs
field_styles = coloredlogs.DEFAULT_FIELD_STYLES
fmt = '%(asctime)s [%(levelname)-8s] %(name)-4s %(message)s'
datefmt = '%H:%M'

# surpress matplotlib debug bloat
logging.getLogger('matplotlib').setLevel(logging.WARNING)

dictConfig(logging_config)
logger = logging.getLogger(__name__)

coloredlogs.install(level='DEBUG', fmt=fmt, datefmt=datefmt)

for d in [model_path, dataset_path, figure_path]:
    create_dir(d)

# constants
EPS = 1e-8

import tensorflow as tf

has_gpu = tf.test.is_gpu_available(cuda_only=True)
hostname = socket.gethostname()

if has_gpu:
    logger.info(textbf(textcolor('Using GPU for training.', color='green')))
else:
    if hostname == 'llach-macos':
        logger.critical('ONLY TRAINING ON CPU!!!')
    else:
        logger.critical('ONLY TRAINING ON CPU!!! sleeping for two seconds')
        time.sleep(2)
