from DeepLibphys.utils.functions.common import *
from novainstrumentation import smooth
import numpy as np
import scipy.io as sio
import time
import threading
import time
import argparse

from DeepLibphys.utils.functions.signal2model import Signal2Model
import DeepLibphys.models_tf.LibphysGRU_dev as GRU

def get_variables(param):
    if param == "arr":
        return RAW_SIGNAL_DIRECTORY + 'MIT-Arrythmia', '../data/processed/biometry_mit[256].npz', 'ecg_mit_arrythmia_'
    if param == "sinus":
        return RAW_SIGNAL_DIRECTORY + 'MIT-Sinus', '../data/processed/biometry_mit_sinus[256].npz', 'ecg_mit_sinus_'
    if param == "long":
        return RAW_SIGNAL_DIRECTORY + 'MIT-Long-Term', '../data/processed/biometry_mit_long_term[256].npz', \
                'ecg_mit_long_term_'

    return None

def get_file_tag(core_name, dataset=-5, epoch=-5):
    """
    Gives a standard name for the file, depending on the #dataset and #epoch
    :param dataset: - int - dataset number
                    (-1 if havent start training, -5 when the last batch training condition was met)
    :param epoch: - int - the last epoch number the dataset was trained
                    (-1 if havent start training, -5 when the training condition was met)
    :return: file_tag composed as GRU_SIGNALNAME[SD.HD.BTTT.DATASET.EPOCH] -> example GRU_ecg[64.16.0.-5]
    """

    return 'GRU_{0}[{1}.{2}.{3}.{4}.{5}].npz'.\
                format(core_name, 256, 256, -1, dataset, epoch)


GRU_DATA_DIRECTORY = "/media/belo/Storage/owncloud/Research Projects/DeepLibphys/Current Trained/"


def try_to_load(core_name, signal2model, ):
    dir_name = "ECG_BIOMETRY[MIT]"
    search_dir = GRU_DATA_DIRECTORY + dir_name + '/'
    files = os.listdir(search_dir)
    try:
        files.index(get_file_tag(core_name))
        model = GRU.LibphysGRU(signal2model)
        model.load(dir_name=dir_name)
        print("Found!")
        return model
    except ValueError:
        for x in range(3500, 0, -250):
            try:
                files.index(get_file_tag(core_name))
                model = GRU.LibphysGRU(signal2model)
                model.load(dir_name=dir_name, file_tag=model.get_file_tag(0, x))
                print("Loaded! epoch {0}".format(x))
                return model
            except ValueError:
                pass
    if os.path.exists(search_dir + "backup/" + get_file_tag(core_name)):
        model = GRU.LibphysGRU(signal2model)
        model.load(dir_name="ECG_BIOMETRY[MIT]/backup")
        return model
    else:
        return GRU.LibphysGRU(signal2model)


def process_and_save_signals(filenames, core_names, Ns, indexes2process=None):
    if indexes2process is None:
        indexes2process = np.arange(len(filenames))

    all_indexes = np.arange(len(filenames))
    for x, filename, core_name in \
            zip(all_indexes[indexes2process], filenames[indexes2process], core_names[indexes2process]):
        print(x)
        try:
            print("Loading signal {0}...".format(x))
            if x < Ns[0]:
                fs = 360
            else:
                fs = 128
            original_signal = sio.loadmat(filename)['val'][0][3000:3000 + 3600 * fs]

            signal = smooth(original_signal, 10)
            signal = signal - smooth(signal, fs)

            time = len(original_signal) / fs
            N = len(original_signal)
            iter_ = scp.interpolate.interp1d(np.arange(0, time, 1 / fs), signal)
            t = np.arange(0, time - 1 / 250, 1 / 250)
            signal = iter_(t)

            moving_maximum = np.array(moving_max(signal, int(360 * 1.2)))
            moving_maximum = smooth(moving_maximum, int(360))
            signal = signal / moving_maximum

            signal = process_dnn_signal(signal, 256, window_rmavg=None, confidence=0.01)
            # new_path = '../data/processed/MIT/{0}[256].npz'.format(core_name)
            # np.savez(new_path, signal=signal, core_name=core_name)

        except:
            print("error")
            pass


def get_processing_variables():
    a_dir, apdp, core_name0 = get_variables('arr')
    s_dir, spdp, core_name1 = get_variables('sinus')
    l_dir, lpdp, core_name2 = get_variables('long')

    # full_paths = os.listdir(mit_dir)
    filenames = [a_dir + "/" + full_path for full_path in os.listdir(a_dir) if full_path.endswith(".mat")] + \
                [s_dir + "/" + full_path for full_path in os.listdir(s_dir) if full_path.endswith(".mat")] + \
                [l_dir + "/" + full_path for full_path in os.listdir(l_dir) if full_path.endswith(".mat")]

    Ns = [len(np.load(apdp)["signals"]), len(np.load(spdp)["signals"].tolist()), len(np.load(lpdp)["signals"].tolist())]

    core_names = []
    for x, n in enumerate(Ns):
        for i in range(n):
            core_names.append(eval("core_name{0}".format(x)) + str(i))

    # print(Ns)

    return filenames, Ns, np.array(core_names)


def start(s):
    signal_dim = 256
    hidden_dim = 256
    mini_batch_size = 2
    batch_size = 8
    window_size = 16
    save_interval = 250
    signal_directory = 'ECG_BIOMETRY[MIT_test]'.format(256, window_size)

    raw_filenames, Ns, core_names = get_processing_variables()

    # process_and_save_signals(raw_filenames, core_names, Ns, indexes2process=np.arange(45, len(raw_filenames)))
    # exit()

    processed_filenames = np.array(['../data/processed/MIT/{0}[256].npz'.format(core_name) for core_name in core_names])
    x_trains, y_trains, signals_2_models = [], [], []

    # indexes = np.array([1, 7, 11, 12, 20, 30, 32, 33, 42] + list(range(Ns[0] + 2, sum(Ns) + 1))) - 1
    # s = indexes.tolist().index(29)
    ind = np.arange(0, len(processed_filenames))
    ind = np.array([1, 3, 4, 5, 6, 7, 8 , 9 , 10])
    # s = 2
    step = 5
    e = s*step + step
    ind = ind[s*step:e]
    # indexes = [48, 49]
    # indexes = np.array([0, 6, 11, 17, 26, 36, 37, 38])#, 41, 51, 55])
    # ind = np.array([indexes[s]])
    print(str(np.arange(s*step, e)) + " - " + str(ind))

    for i, filename in enumerate(processed_filenames[ind]):
        signal, core_name = np.load(filename)["signal"], np.load(filename)["core_name"]
        running_ok = False
        signal2model = Signal2Model(core_name, signal_directory, signal_dim=signal_dim, number_of_epochs=3000,
                                    hidden_dim=hidden_dim, learning_rate_val=0.01, n_grus=3,
                                    batch_size=batch_size, mini_batch_size=mini_batch_size, window_size=window_size+1,
                                    save_interval=save_interval, lower_error=1e-10, count_to_break_max=3)
        last_index = int(len(signal) * 0.33)
        std_tol = 0.1
        mean_tol = 0.1
        n_runs = 0
        while not running_ok:
            print("Initiating training... ")
            x_train, y_train = prepare_test_data([signal[:last_index]], signal2model, mean_tol=mean_tol, std_tol=std_tol)

            indexes = range(len(x_train) - (len(x_train) % mini_batch_size))

            signal2model.batch_size = len(indexes)
            signal2model.window_size = window_size

            print("Compiling Model {0}".format(signal2model.model_name))

            # if n_runs < 2:
            #     model = try_to_load(core_name, signal2model)
            # else:
            model = GRU.LibphysGRU(signal2model)
            # model.debug = True
            if model:

                returned = model.train(x_train[indexes], y_train[indexes])
                # if i == 16:
                if returned:
                    model.save(signal2model.signal_directory, model.get_file_tag(-5, -5))
                else:
                    std_tol += 0.05

                running_ok = returned
                n_runs += 1
            else:
                running_ok = True


# start(int(sys.argv[1]))
# print("Ended process "+sys.argv[1])
start(0)
# for s in range(1, 3):
#     x = threading.Thread(target=start, args=[s])
#
#     x.start()
    # time.sleep(3*60)