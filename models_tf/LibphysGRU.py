import tensorflow as tf
import time
import novainstrumentation as ni
from DeepLibphys.utils.functions.signal2model import Signal2Model, ModelInfo
import numpy as np
from tensorflow.python.client import timeline
import matplotlib.pyplot as plt
import os
import seaborn as sea
import DeepLibphys.models_tf.CONFIG as CONFIG
import labnotebook


class TrainingStatus:
    OK, LOWER_RELATIVE_LOSS_REACHED, LOWER_LOSS_REACHED, MAXIMUM_ATTEMPTS_REACHED, LOWER_LEARNING_RATE_REACHED, \
    LOWER_MEAN, MAX_EPOCHS_REACHED, RELATIVE_POSITIVE_LOSS,  = range(8)


class LibphysGRU:
    def __init__(self, signal2model=None):
        super().__init__(signal2model)

    def get_common_variables(self):
        self.gru_indexes = tf.reshape(tf.range(self.signal2model.n_grus, dtype=np.int32), (self.signal2model.n_grus, 1))
        self.n_batches = tf.constant(int(self.signal2model.batch_size / self.signal2model.mini_batch_size),
                                     dtype=tf.int16, name="n_batches")

        self.trainables = self.parameters

    def get_specific_variables(self):
        Hd = self.signal2model.hidden_dim
        Ns = self.signal2model.n_signals

        CW = np.random.uniform(-np.sqrt(1. / (Hd * Hd)), np.sqrt(1. / (Hd * Hd)),
                               (3, (Hd * Ns), (Hd * Ns)))
        CU = np.random.uniform(-np.sqrt(1. / (Hd * Ns)), np.sqrt(1. / (Hd * Ns)),
                               (3, (Hd * Ns), (Hd * Ns)))
        Cb = np.zeros((3, Hd * Ns))

        self.CU = tf.Variable(CU, trainable=True, dtype=tf.float32, name="CU")
        self.CW = tf.Variable(CW, trainable=True, dtype=tf.float32, name="CW")
        self.Cb = tf.Variable(Cb, trainable=True, dtype=tf.float32, name="Cb")

        self.central_parameters = [self.CU, self.CW, self.Cb]
        self.trainables = self.parameters + self.central_parameters

    def GRUnn(self, out_prev, x_t):
        E, U, W, V, b, c, gru_indexes = self.E, self.U, self.W, self.V, self.b, self.c, self.gru_indexes
        Hd, Sd, Bd = self.signal2model.hidden_dim, self.signal2model.signal_dim, tf.shape(x_t)[0]
        coversion_ones = tf.ones((1, Bd), dtype=tf.float32, name="conversion_matrix")
        # s_prev, o_prev, l_prev = out_prev
        s_prev, o_prev = out_prev

        def GRU(last_input, gru_params):
            s_g_prev, u, w, b = gru_params

            z = tf.nn.sigmoid(tf.matmul(u[0], last_input) +
                              tf.matmul(w[0], s_g_prev) +
                              tf.matmul(tf.reshape(b[0], (Hd, 1)), coversion_ones))

            r = tf.nn.sigmoid(tf.matmul(u[1], last_input) + tf.matmul(w[1], s_g_prev) +
                              tf.matmul(tf.reshape(b[1], (Hd, 1)), coversion_ones))

            value = tf.matmul(u[2], last_input) + tf.matmul(w[2], s_g_prev * r) + \
                    tf.matmul(tf.reshape(b[2], (Hd, 1)), coversion_ones)
            s_candidate = tf.nn.tanh(value)
            output = tf.add(((tf.ones_like(z) - z) * s_candidate), (z * s_g_prev), name="out_GRU")

            return output

        # x_e -> (Hd x Mb)
        x_e = tf.gather(self.E, x_t, axis=1)
        s_t_ = []

        s_t_.append(GRU(x_e, [s_prev[0], U[0], W[0], b[0]]))
        s_t_.append(GRU(s_t_[0], [s_prev[1], U[1], W[1], b[1]]))
        s_t_.append(GRU(s_t_[1], [s_prev[2], U[2], W[2], b[2]]))
        s_t = tf.stack(s_t_)
        # tf.scan(GRU, (s_prev, self.U, self.W, self.b), initializer=x_e, parallel_iterations=1, name="states")

        logits = tf.matmul(self.V, s_t[-1]) + tf.matmul(tf.reshape(self.c, (Sd, 1)), coversion_ones)

        o_t = tf.nn.softmax(logits, axis=2)

        return [s_t, o_t]#, logits]

    def feed_forward_predict(self, x_batch):
        initial_s = tf.zeros((self.signal2model.n_grus, self.signal2model.hidden_dim, tf.shape(x_batch)[1]), dtype=np.float32)
        initial_out = tf.zeros((self.signal2model.signal_dim, tf.shape(x_batch)[1]), dtype=np.float32)
        # initial_l = tf.zeros((self.signal2model.signal_dim, tf.shape(x_batch)[1]), dtype=np.float32)

        # x_batch = (N x Bd) - N (samples); Bd - Batch dimension
        # [s, o, l] = tf.scan(self.GRUnn, x_batch, initializer=[initial_s, initial_out, initial_l], parallel_iterations=1,
        #                     name="network_output")
        [_, o] = tf.scan(self.GRUnn, x_batch, initializer=[initial_s, initial_out], parallel_iterations=1,
                            name="network_output")
        return o

    def feed_forward_predict_with_states(self, x_batch):
        initial_s = tf.zeros((self.signal2model.n_grus, self.signal2model.hidden_dim, tf.shape(x_batch)[1]),
                             dtype=np.float32)
        initial_out = tf.zeros((self.signal2model.signal_dim, tf.shape(x_batch)[1]), dtype=np.float32)
        # initial_l = tf.zeros((self.signal2model.signal_dim, tf.shape(x_batch)[1]), dtype=np.float32)

        # x_batch = (N x Bd) - N (samples); Bd - Batch dimension
        # [s, o, l] = tf.scan(self.GRUnn, x_batch, initializer=[initial_s, initial_out, initial_l], parallel_iterations=1,
        #                     name="network_output")
        [s, o] = tf.scan(self.GRUnn, x_batch, initializer=[initial_s, initial_out], parallel_iterations=1,
                         name="network_output")
        return [s, o]

    def calculate_predictions(self):
        # MAP NOT WORKING:
        # shape(X)[0] -> Windows
        # shape(X)[1] -> Samples
        # n_batches = int(signal2model.batch_size / self.signal2model.mini_batch_size)
        # N = tf.shape(self.X)[1]
        # print(X)
        # get the matrices from E with tf.gather(E, X, axis=1, name="X_e")
        # transpose these matrices for (batch_size, HD, N)
        # reshape to enter map, where each minibatch is entered at the same time (n_batches, mini_batch, HD, N)
        # transpose to enter the DNN inside -> (n_batches, N, mini_batch)

        return self.feed_forward_predict(tf.transpose(self.X))

    def to_one_hot_vector_in_mini_batches(self, matrix):
        return self.get_one_hot(matrix)

    def get_one_hot(self, columns):
        return tf.gather(self.identity, columns)


    def calculate_cross_entropy(self):
        return None
        # logits = tf.transpose(self.logits, perm=[2, 0, 1])
        # n_batches = int(self.signal2model.batch_size / self.signal2model.mini_batch_size)
        # y = tf.reshape(self.Y, (n_batches, self.signal2model.mini_batch_size, tf.shape(self.Y)[1]))
        # self.full_loss = tf.losses.sparse_softmax_cross_entropy_with_logits(labels=y, logits=logits)
        # return self.full_loss

    def calculate_mse(self):
        _y = self.to_one_hot_vector_in_mini_batches(self.Y)
        return tf.reduce_mean(tf.subtract(tf.transpose(self.out, perm=[2, 0, 1]), _y) ** 2, axis=2, name="mse")



    def calculate_mse_vector_loss(self, x, y):
        with tf.variable_scope('vector_loss'):
            return tf.reduce_mean(self.calculate_minibatch_mse(x, y), axis=0, name="vector_loss")

    def calculate_mse_loss(self):
        return tf.reduce_mean(self.calculate_minibatch_mse(), axis=0, name="loss")

    def define_status(self):
        lower_error_threshold, higher_error_threshold = [1e-9, 1]

        if self.learning_rate < self.signal2model.lower_learning_rate:
            if self.debug:
                print("LOWER_LEARNING_RATE_REACHED")
            return TrainingStatus.LOWER_LEARNING_RATE_REACHED
        if self.epoch > self.signal2model.number_of_epochs:
            if self.debug:
                print("MAX_EPOCHS_REACHED")
            return TrainingStatus.MAX_EPOCHS_REACHED
        if self.loss_history[-1] < self.signal2model.lower_error:
            if self.debug:
                print("LOWER_LOSS_REACHED")
            return TrainingStatus.LOWER_LOSS_REACHED

        mean_history = np.mean(np.array(self.loss_history)[-self.mean_count:])
        last_mean_history = np.mean(np.array(self.loss_history)[-self.mean_count*2:-self.mean_count])
        relative_loss_gradient = (self.loss_history[-1] - self.loss_history[-2]) / mean_history

        if self.debug:
            print("Loss: {0} - {1}: {2}".format(self.loss_history[-1], self.loss_history[-2], relative_loss_gradient))

        if self.count_up_slope > self.signal2model.count_to_break_max:
            print("Is the mean loss x100 {0:.5f} < {1:.5f}?".format(mean_history, last_mean_history))
            if mean_history < last_mean_history:
                if self.debug:
                    print("LOWER_MEAN")
                return TrainingStatus.LOWER_MEAN
            else:
                self.count_up_slope = 0
        if abs(relative_loss_gradient) < lower_error_threshold:
            if self.debug:
                print("LOWER_RELATIVE_LOSS_REACHED")
            return TrainingStatus.LOWER_RELATIVE_LOSS_REACHED
        if relative_loss_gradient > 0:
            if self.debug:
                print("NEGATIVE_LOSS")
            return TrainingStatus.RELATIVE_POSITIVE_LOSS

        if self.debug:
            print("OK")

        return TrainingStatus.OK

    def calculate_learning_rate_and_control_sequence(self):
        self.train_time = int((time.time() - self.init_time))
        print("Current Loss x100: {0:.6f} @ Time: {1:.1f} min; epoch: {2}".format(self.loss_history[-1]*100,
                                                                              self.train_time/60, self.epoch))

        if self.epoch < self.mean_count:
            return True

        if self.epoch % self.signal2model.save_interval == 0:
            self.save(self.get_file_tag(self.batch_size))

        status = self.define_status()

        if status == TrainingStatus.LOWER_MEAN:
            self.learning_rate = self.learning_rate * 4 / 5
            self.learning_rate_gpu = self.learning_rate_gpu * 4 / 5
            print("Adjusting learning rate: " + str(self.learning_rate))
            self.count_up_slope = 0
            if self.debug:
                print("count up")
            return True
        elif status == TrainingStatus.LOWER_LOSS_REACHED:
            print("Lower loss reached")
            return False
        elif status == TrainingStatus.RELATIVE_POSITIVE_LOSS:
            if self.debug:
                print("count up")
            self.count_up_slope += 1
            return True
        elif status == TrainingStatus.MAX_EPOCHS_REACHED:
            print("Maximum epochs reached")
            return False
        elif status == TrainingStatus.LOWER_RELATIVE_LOSS_REACHED:
            self.count_up_slope += 1
            print("Lower relative loss reached")
            return True
        elif status == TrainingStatus.OK:
            return True
        elif TrainingStatus.LOWER_LEARNING_RATE_REACHED:
            print("Lower learning rate reached - Training reached an end")
        elif TrainingStatus.MAX_EPOCHS_REACHED:
            print("Last epoch reached - Training reached an end")
        elif TrainingStatus.LOWER_LOSS_REACHED:
            print("Lower loss reached - Training reached an end")

        return False

    @property
    def loss_op(self):
        """ An Operation that takes one optimization step. """
        return self.loss

    @property
    def optimize_op(self):
        """ An Operation that takes one optimization step. """
        return self._optimize_op

    def shuffle(self, X, Y, random_indexes):
        return {self.X: X[random_indexes],
                self.Y: Y[random_indexes]}

    def train(self, X, Y, signal2model=None):
        self.batch_size += np.shape(X)[0]
        self.init_time = time.time()
        plt.ion()
        if signal2model is not None:
            self.signal2model = signal2model
        plt.ion()
        condition_not_met = True
        history = []
        self.epoch = 0
        self.loss_history = []
        tf.summary.scalar('loss', self.loss)
        merged = tf.summary.merge_all()
        train_writer = tf.summary.FileWriter('train',
                                             self.session.graph)
        # run_options = tf.RunOptions(report_tensor_allocations_upon_oom=True)

        db_url = 'postgres://belo:passsword@localhost/postgres'
        experiments, steps, model_params = labnotebook.initialize(db_url)
        model_desc = {'loss': 0.}
        experiment = labnotebook.start_experiment(model_desc=model_desc)
        tf.global_variables_initializer()
        while condition_not_met:
            self.epoch += 1
            # tic = time.time()
            random_indexes = np.random.permutation(self.signal2model.batch_size)
            groups = np.reshape(random_indexes,
                                (int(self.signal2model.batch_size/self.signal2model.mini_batch_size),
                                 self.signal2model.mini_batch_size))
            for group in groups:
                dictionary = self.shuffle(X, Y, group)
                op, group_loss = self.session.run(
                    [self.optimize_op, self.loss_op],
                    feed_dict=dictionary)#, options=run_options)
                labnotebook.step_experiment(experiment,
                                            timestep=str(self.epoch),
                                            trainacc=0,
                                            valacc=0,
                                            trainloss=str(group_loss))
                # print("toc: {0} secs".format(time.time()-tic))


            # new_tic = time.time()
            full_loss = self.session.run(
                self.loss_op,
                {self.X: X,
                 self.Y: Y}
            )
            self.loss_history.append(full_loss)
            # labnotebook.step_experiment(experiment,
            #                             timestep=str(self.epoch),
            #                             trainacc=0,
            #                             valacc=0,
            #                             trainloss=str(group_loss),
            #                             custom_fields={'train time': self.train_time,
            #                                            "full loss": full_loss})
            plt.clf()
            if len(history) > 20:
                plt.plot(ni.smooth(np.array(self.loss_history), 20, window="flat"))
            plt.plot(self.loss_history)
            plt.ylim([0, np.max(self.loss_history)])
            plt.pause(0.02)
            # print("loss toc: {0} secs".format(time.time() - new_tic))
            # train_writer.add_summary(info, epoch)
            # print(full_loss)

            condition_not_met = self.calculate_learning_rate_and_control_sequence()
            # print(condition_not_met)
            # condition_not_met = self.signal2model.number_of_epochs > epoch
            # # print(condition_not_met)
            # history.append(full_loss)
            # plt.clf()
            # plt.plot(history)
            # if len(history) > 20:
            #     plt.plot(ni.smooth(np.array(history), 20, window="flat"))
            # plt.pause(0.01)
            # print(self.loss)

        self.train_time = self.start_time - time.time()
        plt.figure()
        plt.plot(self.loss_history)
        plt.show()
        return True
        # labnotebook.end_experiment(experiment,
        #                            final_trainloss=full_loss)

    def make_vector_into_matrix(self, z, N):
        return np.array(np.reshape(z, (np.shape(z)[0], 1)) * np.ones((1, N)))


    def quantize(self, z):
        z -= self.make_vector_into_matrix(np.min(z, axis=1), np.shape(z)[1])
        z = np.round(z * (self.signal2model.signal_dim - 1) / self.make_vector_into_matrix(np.max(z, axis=1), np.shape(z)[1]))

        return np.asarray(z, dtype=np.int32)

    def variable_summaries(var):
      """Attach a lot of summaries to a Tensor (for TensorBoard visualization)."""
      with tf.name_scope('summaries'):
        mean = tf.reduce_mean(var)
        tf.summary.scalar('mean', mean)
        with tf.name_scope('stddev'):
          stddev = tf.sqrt(tf.reduce_mean(tf.square(var - mean)))
        tf.summary.scalar('stddev', stddev)
        tf.summary.scalar('max', tf.reduce_max(var))
        tf.summary.scalar('min', tf.reduce_min(var))
        tf.summary.histogram('histogram', var)

    def save(self, file_tag=None, dir_name=None):
        """
        Saves the model according to the file_tag
        :param dir_name: -string - directory name where the corresponding to the model for saving is
                            -> may use model.get_directory_tag(directory_name, batch_size, window_size)
                            -> if given None it will have the value model.get_directory_tag(model_name, 0, 0)

        :param file_tag: - string - file_tag corresponding to the model for loading
                            -> use model.get_file_tag(dataset, epoch)
                            -> if given None it will assume that is the last version of the model get_file_tag(-5,-5)
        :return: None
        """

        if file_tag is None:
            file_tag = self.get_file_tag(-5, -5)

        if dir_name is None:
            dir_name = self.signal2model.signal_directory

        dir_name = CONFIG.GRU_DATA_DIRECTORY + dir_name + '/'

        if not os.path.exists(dir_name):
            os.makedirs(dir_name)

        filename = dir_name + file_tag + '.npz'
        print("Saving model to file: " + filename)

        [E, U, W, V, b, c] = self.session.run(self.parameters)
        np.savez(filename,
                 E=E,
                 U=U,
                 W=W,
                 V=V,
                 b=b,
                 c=c,
                 signal2model=self.signal2model,
                 train_time=self.train_time,
                 start_time=self.start_time,
                 batch_size=self.batch_size,
                 epoch=self.epoch
                 )

    def load_from_theano(self, file_tag=None, dir_name=None):
        """
        Loads the model

        :param dir_name: -string - directory name where the corresponding to the model for loading is
                            -> may use model.get_directory_tag(dataset, epoch)

        :param file_tag: - string - file_tag corresponding to the model for loading
                            -> use model.get_file_tag(dataset, epoch)
                            if given None it will assume that is the last version of the model get_file_tag(-5,-5)
        :return: None
        """

        print("Starting sinal loading...")
        if file_tag is None:
            file_tag = self.get_file_tag(-5, -5)

        if dir_name is None:
            dir_name = self.signal2model.signal_directory

        dir_name = CONFIG.GRU_DATA_DIRECTORY + dir_name + '/'

        npzfile = np.load(dir_name + file_tag + ".npz")

        # for parameter, name in zip(self.parameters, self.parameters_names):
        self.E = tf.Variable(npzfile["E"], trainable=True, dtype=tf.float32, name="E")
        print("E")
        self.U = tf.Variable(np.reshape(npzfile["U"], (3, 3, self.signal2model.hidden_dim, self.signal2model.hidden_dim)),
                             trainable=True, dtype=tf.float32, name="U")
        print("U")
        self.W = tf.Variable(np.reshape(npzfile["W"], (3, 3, self.signal2model.hidden_dim, self.signal2model.hidden_dim)),
                             trainable=True, dtype=tf.float32, name="W")
        print("W")
        self.V = tf.Variable(npzfile["V"], trainable=True, dtype=tf.float32, name="V")
        print("V")
        self.b = tf.Variable(np.reshape(npzfile["b"], (3, 3, self.signal2model.hidden_dim)),
                             trainable=True, dtype=tf.float32, name="b")
        print("b")
        self.c = tf.Variable(npzfile["c"], trainable=True, dtype=tf.float32, name="c")

        print("c")
        self.session.run(tf.initialize_all_variables())

        try:
            self.train_time = npzfile["train_time"]
        except:
            print("Error loading variable {0}".format("train_time"))

        try:
            self.start_time = npzfile["start_time"]
        except:
            print("Error loading variable {0}".format("start_time"))

        try:
            self.batch_size = npzfile["batch_size"]
        except:
            print("Error loading variable {0}".format("batch_size"))

        try:
            self.epoch = npzfile["epoch"]
        except:
            print("Error loading variable {0}".format("epoch"))

    def load(self, file_tag=None, dir_name=None):
        """
        Loads the model

        :param dir_name: -string - directory name where the corresponding to the model for loading is
                            -> may use model.get_directory_tag(dataset, epoch)

        :param file_tag: - string - file_tag corresponding to the model for loading
                            -> use model.get_file_tag(dataset, epoch)
                            if given None it will assume that is the last version of the model get_file_tag(-5,-5)
        :return: None
        """

        print("Starting sinal loading...")
        if file_tag is None:
            file_tag = self.get_file_tag(-5, -5)

        if dir_name is None:
            dir_name = self.signal2model.signal_directory

        dir_name = CONFIG.GRU_DATA_DIRECTORY + dir_name + '/'

        npzfile = np.load(dir_name + file_tag + ".npz")

        # for parameter, name in zip(self.parameters, self.parameters_names):
        self.E = tf.Variable(npzfile["E"], trainable=True, dtype=tf.float32, name="E")
        print("E")
        self.U = tf.Variable(npzfile["U"], trainable=True, dtype=tf.float32, name="U")
        print("U")
        self.W = tf.Variable(npzfile["W"], trainable=True, dtype=tf.float32, name="W")
        print("W")
        self.V = tf.Variable(npzfile["V"], trainable=True, dtype=tf.float32, name="V")
        print("V")
        self.b = tf.Variable(npzfile["b"], trainable=True, dtype=tf.float32, name="b")
        print("b")
        self.c = tf.Variable(npzfile["c"], trainable=True, dtype=tf.float32, name="c")

        print("c")
        self.session.run(tf.initialize_all_variables())

        try:
            self.train_time = npzfile["train_time"]
        except:
            print("Error loading variable {0}".format("train_time"))

        try:
            self.start_time = npzfile["start_time"]
        except:
            print("Error loading variable {0}".format("start_time"))

        try:
            self.batch_size = npzfile["batch_size"]
        except:
            print("Error loading variable {0}".format("batch_size"))

        try:
            self.epoch = npzfile["epoch"]
        except:
            print("Error loading variable {0}".format("epoch"))

    @staticmethod
    def load_full_model(self, model_name, dir_name, hidden_dim, signal_dim, dataset=-5, epoch=-5):
        """
        Loads the model

        :param dir_name: -string - directory name where the corresponding to the model for loading is
                            -> may use model.get_directory_tag(dataset, epoch)

        :param file_tag: - string - file_tag corresponding to the model for loading
                            -> use model.get_file_tag(dataset, epoch)
                            if given None it will assume that is the last version of the model get_file_tag(-5,-5)
        :return: None
        """

        print("Starting sinal loading...")

        file_tag = self.get_static_file_tag(model_name, signal_dim, hidden_dim, dataset, epoch)

        signal2model = np.load(CONFIG.GRU_DATA_DIRECTORY + dir_name + '/' + file_tag + ".npz")["signal2model"]
        model = LibphysGRU(signal2model)
        model.load(file_tag, dir_name)
        return model


    def get_file_tag(self, dataset=-5, epoch=-5, bptt_truncate=-1):
        """
        Gives a standard name for the file, depending on the #dataset and #epoch
        :param dataset: - int - dataset number
                        (-1 if havent start training, -5 when the last batch training condition was met)
        :param epoch: - int - the last epoch number the dataset was trained
                        (-1 if havent start training, -5 when the training condition was met)
        :return: file_tag composed as GRU_SIGNALNAME[SD.HD.BTTT.DATASET.EPOCH] -> example GRU_ecg[64.16.0.-5]
        """

        return 'GRU_{0}[{1}.{2}.{3}.{4}.{5}]'.\
                    format(self.signal2model.model_name, self.signal2model.signal_dim, self.signal2model.hidden_dim,
                           bptt_truncate, dataset, epoch)

    @staticmethod
    def get_static_file_tag(model_name, signal_dim, hidden_dim, dataset=-5, epoch=-5, bptt_truncate=-1):
        """
        Gives a standard name for the file, depending on the #dataset and #epoch
        :param dataset: - int - dataset number
                        (-1 if havent start training, -5 when the last batch training condition was met)
        :param epoch: - int - the last epoch number the dataset was trained
                        (-1 if havent start training, -5 when the training condition was met)
        :return: file_tag composed as GRU_SIGNALNAME[SD.HD.BTTT.DATASET.EPOCH] -> example GRU_ecg[64.16.0.-5]
        """

        return 'GRU_{0}[{1}.{2}.{3}.{4}.{5}]'.\
                    format(model_name, signal_dim, hidden_dim, bptt_truncate, dataset, epoch)


    def get_directory_tag(self, dir_name=None, B=128, W=256):
        """
        Gives a standard name to the directoy.

        :param dir_name: - string - TAG for the directory name - discribing the dataset for training
        :param B: - int - Batch size
        :param W: - int - Window size

        :return: Standard directory name composed as TAG[B.W] -> example ECG[256.128]
        """
        if dir_name is None:
            dir_name = self.model_name.upper()

        return dir_name+'[{0}.{1}]'.format(B, W)

