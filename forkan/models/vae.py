import time
import json
import numpy as np
import tensorflow as tf

import logging
import datetime

from tqdm import tqdm
from tabulate import tabulate

from forkan import model_path
from forkan.common import CSVLogger
from forkan.common.utils import print_dict, create_dir
from forkan.common.tf_utils import scalar_summary
from forkan.models.vae_networks import build_network


class VAE(object):

    def __init__(self, input_shape=None, name='default', network='atari', latent_dim=20, beta=44.4, lr=5e-3,
                 load_from=None):

        if input_shape is None:
            assert load_from is not None, 'input shape need to be given if no model is loaded'

        # take care of correct input dim: (BATCH, HEIGHT, WIDTH, CHANNELS)
        # add channel dim if not provided
        if len(input_shape) == 2:
            input_shape = input_shape + (1,)

        # add batch dim
        self.input_shape = (None,) + input_shape

        self.latent_dim = latent_dim
        self.network = network
        self.beta = beta
        self.lr = lr

        self.num_channels = self.input_shape[-1]
        self.log = logging.getLogger('vae')

        if load_from is None: # fresh vae
            self.savename = '{}-b{}-lat{}-lr{}-{}'.format(network, beta, latent_dim, lr,
                                                          datetime.datetime.now().strftime('%Y-%m-%dT%H:%M'))
            self.savepath = '{}/vae-{}/{}/'.format(model_path, name, self.savename)
            create_dir(self.savepath)

            self.log.info('storing files under {}'.format(self.savepath))

            params = locals()
            params.pop('self')

            with open('{}/params.json'.format(self.savepath), 'w') as outfile:
                json.dump(params, outfile)
        else: # load old parameter

            self.savename = load_from
            self.savepath = '{}/vae-{}/{}/'.format(model_path, name, self.savename)

            self.log.info('loading model and parameters from {}'.format(self.savepath))

            try:
                with open('{}/params.json'.format(self.savepath), 'r') as infile:
                    params = json.load(infile)

                for k, v in params.items():
                    setattr(self, k, v)

            except:
                self.log.critical('loading {}/params.json failed!'.format(self.savepath))
                exit(0)

        self._input = tf.placeholder(tf.float32, shape=self.input_shape, name='x')

        """ TF Graph setup """
        self.mus, self.logvars, self.z, self._output = \
            build_network(self._input, self.input_shape, latent_dim=self.latent_dim, network_type=self.network)
        print('\n')

        """ Loss """
        self.reconstruction_loss = tf.losses.mean_squared_error(self._input, self._output)
        self.dkl_j = -0.5 * (1 + self.logvars - tf.square(self.mus) - tf.exp(self.logvars))
        self.mean_kl_j = tf.reduce_mean(self.dkl_j, axis=0)
        self.dkl_loss = tf.reduce_sum(self.mean_kl_j, axis=0)
        self.scaled_kl = beta * self.dkl_loss
        self.total_loss = self.reconstruction_loss + self.scaled_kl

        # create optimizer
        self.opt = tf.train.AdamOptimizer(learning_rate=self.lr)

        # compute gradients for loss
        self.gradients = self.opt.compute_gradients(self.total_loss)

        # create training op
        self.train_op = self.opt.apply_gradients(self.gradients)

        """ TF setup """
        self.s = tf.Session()
        tf.global_variables_initializer().run(session=self.s)

        # Saver objects handles writing and reading protobuf weight files
        self.saver = tf.train.Saver(var_list=tf.all_variables())

        self.log.info('VAE has parameters:')
        print_dict(params, lo=self.log)

        self._tensorboard_setup()
        csv_header = ['date', '#episode', '#batch', 'loss', 'kl-loss'] + \
                     ['z{}-kl'.format(i) for i in range(self.latent_dim)]
        self.csv = CSVLogger('{}/progress.csv'.format(self.savepath), *csv_header)

    def __del__(self):
        """ cleanup after object finalization """

        # close tf.Session
        if hasattr(self, 's'):
           self.s.close()

    def _save(self, filename='latest'):
        """ Saves current weights """
        weights = '{}/{}'.format(self.savepath, filename)
        self.log.info('saving weights \'{}\''.format(filename))
        self.saver.save(self.s, weights)

    def _tensorboard_setup(self):
        """ Tensorboard (TB) setup """

        self.bps_ph = tf.placeholder(tf.int32, ())
        self.ep_ph = tf.placeholder(tf.int32, ())

        scalar_summary('batches-per-second', self.bps_ph)
        scalar_summary('episode', self.ep_ph)

        mu_mean = tf.reduce_mean(self.mus, axis=0)
        vars_mean = tf.reduce_mean(tf.exp(0.5 * self.logvars), axis=0)

        with tf.variable_scope('loss'):
            scalar_summary('scaled_kl', self.scaled_kl)
            scalar_summary('reconstruction-loss', self.reconstruction_loss)
            scalar_summary('total-loss', self.total_loss)
            scalar_summary('mean-dkl', self.dkl_loss)

        with tf.variable_scope('zj_kl'):
            for i in range(self.latent_dim):
                scalar_summary('z{}-kl'.format(i), self.mean_kl_j[i])

        with tf.variable_scope('zj_mu'):
            for i in range(self.latent_dim):
                scalar_summary('z{}-mu'.format(i), mu_mean[i])

        with tf.variable_scope('zj_var'):
            for i in range(self.latent_dim):
                scalar_summary('z{}-var'.format(i), vars_mean[i])

        # plot network weights
        with tf.variable_scope('weights'):
            for pv in tf.trainable_variables(): tf.summary.histogram('{}'.format(pv.name), pv)

        # gradient histograms
        with tf.variable_scope('gradients'):
            for g in self.gradients:
                if g[0] is not None:
                    tf.summary.histogram('{}-grad'.format(g[1].name), g[0]) # todo why is gradient None

        self.merge_op = tf.summary.merge_all()

        self.writer = tf.summary.FileWriter(self.savepath,
                                            graph=tf.get_default_graph())
            
    def _preprocess_batch(self, batch):
        """ preprocesses batch """

        """ completing batch shape if some dimesions are missing """
        # grayscale, one sample
        if len(batch.shape) == 2:
            batch = np.expand_dims(np.expand_dims(batch, axis=-1), axis=0)
        # either  batch of grascale or single multichannel image
        elif len(batch.shape) == 3:
            if batch.shape == self.input_shape[1:]:  # single frame
                batch = np.expand_dims(batch, axis=0)
            else:  # batch of grayscale
                batch = np.expand_dims(batch, axis=-1)

        assert len(batch.shape) == 4, 'batch shape mismatch'

        return batch

    def encode(self, batch):
        """ encodes frame(s) """

        batch = self._preprocess_batch(batch)
        self.log.info('encoding batch with shape {}'.format(batch.shape))
        return self.s.run([self.mus, self.logvars], feed_dict={self._input: batch})

    def encode_and_sample(self, batch):
        """ encodes frame(s) and samples from dists """

        batch = self._preprocess_batch(batch)
        self.log.info('encoding and sampling zs for batch with shape {}'.format(batch.shape))
        return self.s.run([self.mus, self.logvars, self.z], feed_dict={self._input: batch})

    def decode(self, zs):
        """ dcodes batch of latent representations """

        if len(zs.shape) == 1:
            zs = np.expand_dims(zs, 0)

        assert len(zs.shape) == 2, 'z batch shape mismatch'
        assert zs.shape[-1] == self.latent_dim, 'vae has latent space of {}, got {}'.format(self.latent_dim, zs.shape[-1])

        return self.s.run(self._output, feed_dict={self.z: zs})

    def train(self, dataset, batch_size=32, num_episodes=30, print_freq=10):
        num_samples = len(dataset)

        assert np.max(dataset) <= 1, 'provide normalized dataset!'

        # add dimensions if needed
        if len(dataset.shape) != 4:
            dataset = self._preprocess_batch(dataset)

        self.log.info('Training on {} samples for {} episodes.'.format(num_samples, num_episodes))
        tstart = time.time()
        nb = 1

        # rollout N episodes
        for ep in tqdm(range(num_episodes)):

            # shuffle dataset
            np.random.shuffle(dataset)

            for n, idx in enumerate(tqdm(np.arange(0, num_samples, batch_size))):
                bps = int(nb / (time.time() - tstart))
                x = dataset[idx:min(idx+batch_size, num_samples), ...]
                sum, _, loss, kl_loss, mean_kl_j = self.s.run([self.merge_op, self.train_op, self.total_loss,
                                                               self.dkl_loss, self.mean_kl_j],
                                                              feed_dict={self._input: x, self.bps_ph: bps,
                                                                         self.ep_ph: ep})

                # increase batch counter
                nb += 1

                # write statistics
                self.writer.add_summary(sum, nb)
                self.csv.writeline(
                    datetime.datetime.now().isoformat(),
                    ep,
                    nb,
                    loss,
                    kl_loss,
                    *[z for z in mean_kl_j]
                )

                if n % print_freq == 0:
                    tab = tabulate([
                        ['episode', ep],
                        ['batch', n],
                        ['bps', bps],
                        ['loss', loss],
                        ['dkl_loss', kl_loss]
                    ])

                    print('\n{}'.format(tab))

            self._save()


if __name__ == '__main__':
    from forkan.datasets.dsprites import load_dsprites
    (data, _) = load_dsprites('translation', repetitions=10)
    v = VAE(data.shape[1:], name='test', network='dsprites', beta=30.1, latent_dim=5)
    v.train(data[:160], num_episodes=5, print_freq=20)

