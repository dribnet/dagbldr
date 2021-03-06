# Author: Kyle Kastner
# License: BSD 3-clause
# Ideas from Junyoung Chung and Kyunghyun Cho
# See https://github.com/jych/cle for a library in this style
import numpy as np
from collections import Counter
from scipy.io import loadmat
from scipy.linalg import svd
from functools import reduce
from ..utils import whitespace_tokenizer
import string
import tarfile
import theano
import zipfile
import gzip
import os
import re
import csv
try:
    import cPickle as pickle
except ImportError:
    import pickle

regex = re.compile('[%s]' % re.escape(string.punctuation))


def get_dataset_dir(dataset_name, data_dir=None, folder=None, create_dir=True):
    """ Get dataset directory path """
    if not data_dir:
        data_dir = os.getenv("DAGBLDR_DATA", os.path.join(
            os.path.expanduser("~"), "dagbldr_data"))
    if folder is None:
        data_dir = os.path.join(data_dir, dataset_name)
    else:
        data_dir = os.path.join(data_dir, folder)
    if not os.path.exists(data_dir) and create_dir:
        os.makedirs(data_dir)
    return data_dir


def download(url, server_fname, local_fname=None, progress_update_percentage=5):
    """
    An internet download utility modified from
    http://stackoverflow.com/questions/22676/
    how-do-i-download-a-file-over-http-using-python/22776#22776
    """
    try:
        import urllib
        urllib.urlretrieve('http://google.com')
    except AttributeError:
        import urllib.request as urllib
    u = urllib.urlopen(url)
    if local_fname is None:
        local_fname = server_fname
    full_path = local_fname
    meta = u.info()
    with open(full_path, 'wb') as f:
        try:
            file_size = int(meta.get("Content-Length"))
        except TypeError:
            print("WARNING: Cannot get file size, displaying bytes instead!")
            file_size = 100
        print("Downloading: %s Bytes: %s" % (server_fname, file_size))
        file_size_dl = 0
        block_sz = int(1E7)
        p = 0
        while True:
            buffer = u.read(block_sz)
            if not buffer:
                break
            file_size_dl += len(buffer)
            f.write(buffer)
            if (file_size_dl * 100. / file_size) > p:
                status = r"%10d  [%3.2f%%]" % (file_size_dl, file_size_dl *
                                               100. / file_size)
                print(status)
                p += progress_update_percentage


def check_fetch_uci_words():
    """ Check for UCI vocabulary """
    url = 'https://archive.ics.uci.edu/ml/machine-learning-databases/'
    url += 'bag-of-words/'
    partial_path = get_dataset_dir("uci_words")
    full_path = os.path.join(partial_path, "uci_words.zip")
    if not os.path.exists(partial_path):
        os.makedirs(partial_path)
    if not os.path.exists(full_path):
        # Download all 5 vocabularies and zip them into a file
        all_vocabs = ['vocab.enron.txt', 'vocab.kos.txt', 'vocab.nips.txt',
                      'vocab.nytimes.txt', 'vocab.pubmed.txt']
        for vocab in all_vocabs:
            dl_url = url + vocab
            download(dl_url, os.path.join(partial_path, vocab),
                     progress_update_percentage=1)

            def zipdir(path, zipf):
                # zipf is zipfile handle
                for root, dirs, files in os.walk(path):
                    for f in files:
                        if "vocab" in f:
                            zipf.write(os.path.join(root, f))

            zipf = zipfile.ZipFile(full_path, 'w')
            zipdir(partial_path, zipf)
            zipf.close()
    return full_path


def fetch_uci_words():
    """ Returns UCI vocabulary text. """
    data_path = check_fetch_uci_words()
    all_data = []
    with zipfile.ZipFile(data_path, "r") as f:
        for name in f.namelist():
            if ".txt" not in name:
                # Skip README
                continue
            data = f.read(name)
            data = data.split("\n")
            data = [l.strip() for l in data if l != ""]
            all_data.extend(data)
    return list(set(all_data))


def _parse_stories(lines, only_supporting=False):
    """ Preprocessing code modified from Keras and Stephen Merity
    http://smerity.com/articles/2015/keras_qa.html
    https://github.com/fchollet/keras/blob/master/examples/babi_rnn.py

    Parse stories provided in the bAbi tasks format

    If only_supporting is true, only the sentences that support the answer are
    kept.
    """
    data = []
    story = []
    for line in lines:
        line = line.decode('utf-8').strip()
        nid, line = line.split(' ', 1)
        nid = int(nid)
        if nid == 1:
            story = []
        if '\t' in line:
            q, a, supporting = line.split('\t')
            q = whitespace_tokenizer(q)
            substory = None
            if only_supporting:
                # Only select the related substory
                supporting = map(int, supporting.split())
                substory = [story[i - 1] for i in supporting]
            else:
                # Provide all the substories
                substory = [x for x in story if x]
            data.append((substory, q, a))
            story.append('')
        else:
            sent = whitespace_tokenizer(line)
            story.append(sent)
    return data


def _get_stories(f, only_supporting=False, max_length=None):
    """ Preprocessing code modified from Keras and Stephen Merity
    http://smerity.com/articles/2015/keras_qa.html
    https://github.com/fchollet/keras/blob/master/examples/babi_rnn.py

    Given a file name, read the file, retrieve the stories, and then convert
    the sentences into a single story.

    If max_length is supplied, any stories longer than max_length tokens will be
    discarded.
    """
    data = _parse_stories(f.readlines(), only_supporting=only_supporting)
    flatten = lambda data: reduce(lambda x, y: x + y, data)
    data = [(flatten(story), q, answer) for story, q, answer in data
            if not max_length or len(flatten(story)) < max_length]
    return data


def _vectorize_stories(data, vocab_size, word_idx):
    """ Preprocessing code modified from Keras and Stephen Merity
    http://smerity.com/articles/2015/keras_qa.html
    https://github.com/fchollet/keras/blob/master/examples/babi_rnn.py
    """
    X = []
    Xq = []
    y = []
    for story, query, answer in data:
        x = [word_idx[w] for w in story]
        xq = [word_idx[w] for w in query]
        yi = np.zeros(vocab_size)
        yi[word_idx[answer]] = 1
        X.append(x)
        Xq.append(xq)
        y.append(yi)
    return X, Xq, np.array(y)


def check_fetch_babi():
    """ Check for babi task data

    "Towards AI-Complete Question Answering: A Set of Prerequisite Toy Tasks"
    J. Weston, A. Bordes, S. Chopra, T. Mikolov, A. Rush
    http://arxiv.org/abs/1502.05698
    """
    url = "http://www.thespermwhale.com/jaseweston/babi/tasks_1-20_v1-2.tar.gz"
    partial_path = get_dataset_dir("babi")
    full_path = os.path.join(partial_path, "tasks_1-20_v1-2.tar.gz")
    if not os.path.exists(partial_path):
        os.makedirs(partial_path)
    if not os.path.exists(full_path):
        download(url, full_path, progress_update_percentage=1)
    return full_path


def fetch_babi(task_number=2):
    """ Fetch data for babi tasks described in
    "Towards AI-Complete Question Answering: A Set of Prerequisite Toy Tasks"
    J. Weston, A. Bordes, S. Chopra, T. Mikolov, A. Rush
    http://arxiv.org/abs/1502.05698

    Preprocessing code modified from Keras and Stephen Merity
    http://smerity.com/articles/2015/keras_qa.html
    https://github.com/fchollet/keras/blob/master/examples/babi_rnn.py

    n_samples : 1000 - 10000 (task dependent)

    Returns
    -------
    summary : dict
        A dictionary cantaining data

        summary["stories"] : list
            List of list of ints

        summary["queries"] : list
            List of list of ints

        summary["target"] : list
            List of list of int

        summary["train_indices"] : array
            Indices for training samples

        summary["valid_indices"] : array
            Indices for validation samples

        summary["vocabulary_size"] : int
            Total vocabulary size
    """

    data_path = check_fetch_babi()
    tar = tarfile.open(data_path)
    if task_number == 2:
        challenge = 'tasks_1-20_v1-2/en/qa2_two-supporting-facts_{}.txt'
    else:
        raise ValueError("No other supported tasks at this time")
    # QA2 with 1000 samples
    train = _get_stories(tar.extractfile(challenge.format('train')))
    test = _get_stories(tar.extractfile(challenge.format('test')))

    vocab = sorted(reduce(lambda x, y: x | y, (
        set(story + q + [answer]) for story, q, answer in train + test)))
    # Reserve 0 for masking via pad_sequences
    vocab_size = len(vocab) + 1
    word_idx = dict((c, i + 1) for i, c in enumerate(vocab))
    # story_maxlen = max(map(len, (x for x, _, _ in train + test)))
    # query_maxlen = max(map(len, (x for _, x, _ in train + test)))

    X_story, X_query, y_answer = _vectorize_stories(train, vocab_size, word_idx)
    valid_X_story, valid_X_query, valid_y_answer = _vectorize_stories(
        test, vocab_size, word_idx)
    train_indices = np.arange(len(y_answer))
    valid_indices = np.arange(len(valid_y_answer)) + len(y_answer)

    X_story, X_query, y_answer = _vectorize_stories(train + test, vocab_size,
                                                    word_idx)
    return {"stories": X_story,
            "queries": X_query,
            "target": y_answer,
            "train_indices": train_indices,
            "valid_indices": valid_indices,
            "vocabulary_size": vocab_size}


def check_fetch_lovecraft():
    """ Check for lovecraft data """
    url = 'https://dl.dropboxusercontent.com/u/15378192/lovecraft_fiction.zip'
    partial_path = get_dataset_dir("lovecraft")
    full_path = os.path.join(partial_path, "lovecraft_fiction.zip")
    if not os.path.exists(partial_path):
        os.makedirs(partial_path)
    if not os.path.exists(full_path):
        download(url, full_path, progress_update_percentage=1)
    return full_path


def fetch_lovecraft():
    """ All the fiction text written by H. P. Lovecraft

    n_samples : 40363
    n_chars : 84 (Counting UNK, EOS)
    n_words : 26644 (Counting UNK)

    Returns
    -------
    summary : dict
        A dictionary cantaining data

        summary["data"] : list, shape (40363,)
            List of strings

        summary["words"] : list,
            List of strings

    """
    data_path = check_fetch_lovecraft()
    all_data = []
    all_words = Counter()
    with zipfile.ZipFile(data_path, "r") as f:
        for name in f.namelist():
            if ".txt" not in name:
                # Skip README
                continue
            data = f.read(name)
            data = data.split("\n")
            data = [l.strip() for l in data if l != ""]
            words = [w for l in data for w in regex.sub('', l.lower()).split(
                " ") if w != ""]
            all_data.extend(data)
            all_words.update(words)
    return {"data": all_data,
            "words": all_words.keys()}


def load_mountains():
    """
    H. P. Lovecraft's At The Mountains Of Madness

    Used for tests which need text data

    n_samples : 3575
    n_chars : 75 (Counting UNK, EOS)
    n_words : 6346 (Counting UNK)

    Returns
    -------
    summary : dict
        A dictionary cantaining data

        summary["data"] : list, shape (3575, )
            List of strings

        summary["words"] : list,

    """
    module_path = os.path.dirname(__file__)
    all_words = Counter()
    with open(os.path.join(module_path, 'data', 'mountains.txt')) as f:
        data = f.read()
        data = data.split("\n")
        data = [l.strip() for l in data if l != ""]
        words = [w for l in data for w in regex.sub('', l.lower()).split(
            " ") if l != ""]
        all_words.update(words)
    return {"data": data,
            "words": all_words.keys()}


def check_fetch_fer():
    """ Check that fer faces are downloaded """
    url = 'https://dl.dropboxusercontent.com/u/15378192/fer2013.tar.gz'
    partial_path = get_dataset_dir("fer")
    full_path = os.path.join(partial_path, "fer2013.tar.gz")
    if not os.path.exists(partial_path):
        os.makedirs(partial_path)
    if not os.path.exists(full_path):
        download(url, full_path, progress_update_percentage=1)
    return full_path


def fetch_fer():
    """
    Flattened 48x48 fer faces with pixel values in [0 - 1]

    n_samples : 35888
    n_features : 2304

    Returns
    -------
    summary : dict
        A dictionary cantaining data and image statistics.

        summary["data"] : array, shape (35888, 2304)
            The flattened data for FER

    """
    data_path = check_fetch_fer()
    t = tarfile.open(data_path, 'r')
    f = t.extractfile(t.getnames()[0])
    reader = csv.reader(f)
    valid_indices = 2 * 3859
    data = np.zeros((35888, 48 * 48), dtype="float32")
    target = np.zeros((35888,), dtype="int32")
    header = None
    for n, row in enumerate(reader):
        if n % 1000 == 0:
            print("Reading sample %i" % n)
        if n == 0:
            header = row
        else:
            target[n] = int(row[0])
            data[n] = np.array(map(float, row[1].split(" "))) / 255.
    train_indices = np.arange(23709)
    valid_indices = np.arange(23709, len(data))
    train_mean0 = data[train_indices].mean(axis=0)
    saved_pca_path = os.path.join(get_dataset_dir("fer"), "FER_PCA.npy")
    if not os.path.exists(saved_pca_path):
        print("Saved PCA not found for FER, computing...")
        U, S, V = svd(data[train_indices] - train_mean0, full_matrices=False)
        train_pca = V
        np.save(saved_pca_path, train_pca)
    else:
        train_pca = np.load(saved_pca_path)
    return {"data": data,
            "target": target,
            "train_indices": train_indices,
            "valid_indices": valid_indices,
            "mean0": train_mean0,
            "pca_matrix": train_pca}


def check_fetch_tfd():
    """ Check that tfd faces are downloaded """
    partial_path = get_dataset_dir("tfd")
    full_path = os.path.join(partial_path, "TFD_48x48.mat")
    if not os.path.exists(partial_path):
        os.makedirs(partial_path)
    if not os.path.exists(full_path):
        raise ValueError("Put TFD_48x48 in %s" % str(partial_path))
    return full_path


def fetch_tfd():
    """
    Flattened 48x48 TFD faces with pixel values in [0 - 1]

    n_samples : 102236
    n_features : 2304

    Returns
    -------
    summary : dict
        A dictionary cantaining data and image statistics.

        summary["data"] : array, shape (102236, 2304)
            The flattened data for TFD

    """
    data_path = check_fetch_tfd()
    matfile = loadmat(data_path)
    all_data = matfile['images'].reshape(len(matfile['images']), -1) / 255.
    all_data = all_data.astype(theano.config.floatX)
    train_indices = np.arange(0, 90000)
    valid_indices = np.arange(0, 10000) + len(train_indices) + 1
    test_indices = np.arange(valid_indices[-1] + 1, len(all_data))
    train_data = all_data[train_indices]
    train_mean0 = train_data.mean(axis=0)
    random_state = np.random.RandomState(1999)
    subset_indices = random_state.choice(train_indices, 25000, replace=False)
    saved_pca_path = os.path.join(get_dataset_dir("tfd"), "TFD_PCA.npy")
    if not os.path.exists(saved_pca_path):
        print("Saved PCA not found for TFD, computing...")
        U, S, V = svd(train_data[subset_indices] - train_mean0,
                      full_matrices=False)
        train_pca = V
        np.save(saved_pca_path, train_pca)
    else:
        train_pca = np.load(saved_pca_path)
    return {"data": all_data,
            "train_indices": train_indices,
            "valid_indices": valid_indices,
            "test_indices": test_indices,
            "mean0": train_mean0,
            "pca_matrix": train_pca}


def check_fetch_frey():
    """ Check that frey faces are downloaded """
    url = 'http://www.cs.nyu.edu/~roweis/data/frey_rawface.mat'
    partial_path = get_dataset_dir("frey")
    full_path = os.path.join(partial_path, "frey_rawface.mat")
    if not os.path.exists(partial_path):
        os.makedirs(partial_path)
    if not os.path.exists(full_path):
        download(url, full_path, progress_update_percentage=1)
    return full_path


def fetch_frey():
    """
    Flattened 20x28 frey faces with pixel values in [0 - 1]

    n_samples : 1965
    n_features : 560

    Returns
    -------
    summary : dict
        A dictionary cantaining data and image statistics.

        summary["data"] : array, shape (1965, 560)

    """
    data_path = check_fetch_frey()
    matfile = loadmat(data_path)
    all_data = (matfile['ff'] / 255.).T
    all_data = all_data.astype(theano.config.floatX)
    return {"data": all_data,
            "mean0": all_data.mean(axis=0),
            "var0": all_data.var(axis=0)}


def check_fetch_mnist():
    """ Check that mnist is downloaded. May need fixing for py3 compat """
    # py3k version is available at mnist_py3k.pkl.gz ... might need to fix
    url = 'http://www.iro.umontreal.ca/~lisa/deep/data/mnist/mnist.pkl.gz'
    partial_path = get_dataset_dir("mnist")
    full_path = os.path.join(partial_path, "mnist.pkl.gz")
    if not os.path.exists(partial_path):
        os.makedirs(partial_path)
    if not os.path.exists(full_path):
        download(url, full_path, progress_update_percentage=1)
    return full_path


def fetch_mnist():
    """
    Flattened 28x28 mnist digits with pixel values in [0 - 1]

    n_samples : 70000
    n_feature : 784

    Returns
    -------
    summary : dict
        A dictionary cantaining data and image statistics.

        summary["data"] : array, shape (70000, 784)
        summary["target"] : array, shape (70000,)
        summary["train_indices"] : array, shape (50000,)
        summary["valid_indices"] : array, shape (10000,)
        summary["test_indices"] : array, shape (10000,)

    """
    data_path = check_fetch_mnist()
    f = gzip.open(data_path, 'rb')
    try:
        train_set, valid_set, test_set = pickle.load(f, encoding="latin1")
    except TypeError:
        train_set, valid_set, test_set = pickle.load(f)
    f.close()
    train_indices = np.arange(0, len(train_set[0]))
    valid_indices = np.arange(0, len(valid_set[0])) + train_indices[-1] + 1
    test_indices = np.arange(0, len(test_set[0])) + valid_indices[-1] + 1
    return {"data": np.concatenate((train_set[0], valid_set[0], test_set[0]),
                                   axis=0).astype(theano.config.floatX),
            "target": np.concatenate((train_set[1], valid_set[1], test_set[1]),
                                     axis=0).astype(np.int32),
            "train_indices": train_indices.astype(np.int32),
            "valid_indices": valid_indices.astype(np.int32),
            "test_indices": test_indices.astype(np.int32)}


def check_fetch_binarized_mnist():
    raise ValueError("Binarized MNIST has no labels! Do not use")
    """
    # public version
    url = 'https://github.com/mgermain/MADE/releases/download/ICML2015/'
    url += 'binarized_mnist.npz'
    partial_path = get_dataset_dir("binarized_mnist")
    fname = "binarized_mnist.npz"
    full_path = os.path.join(partial_path, fname)
    if not os.path.exists(partial_path):
        os.makedirs(partial_path)
    if not os.path.exists(full_path):
        download(url, full_path, progress_update_percentage=1)
    # personal version
    url = "https://dl.dropboxusercontent.com/u/15378192/binarized_mnist_%s.npy"
    fname = "binarized_mnist_%s.npy"
    for s in ["train", "valid", "test"]:
        full_path = os.path.join(partial_path, fname % s)
        if not os.path.exists(partial_path):
            os.makedirs(partial_path)
        if not os.path.exists(full_path):
            download(url % s, full_path, progress_update_percentage=1)
    return partial_path
    """


def fetch_binarized_mnist():
    """
    Flattened 28x28 mnist digits with pixel of either 0 or 1, sampled from
    binomial distribution defined by the original MNIST values

    n_samples : 70000
    n_features : 784

    Returns
    -------
    summary : dict
        A dictionary cantaining data and image statistics.

        summary["data"] : array, shape (70000, 784)
        summary["target"] : array, shape (70000,)
        summary["train_indices"] : array, shape (50000,)
        summary["valid_indices"] : array, shape (10000,)
        summary["test_indices"] : array, shape (10000,)

    """
    mnist = fetch_mnist()
    random_state = np.random.RandomState(1999)

    def get_sampled(arr):
        # make sure that a pixel can always be turned off
        return random_state.binomial(1, arr * 255 / 256., size=arr.shape)

    data = get_sampled(mnist["data"]).astype(theano.config.floatX)
    return {"data": data,
            "target": mnist["target"],
            "train_indices": mnist["train_indices"],
            "valid_indices": mnist["valid_indices"],
            "test_indices": mnist["test_indices"]}


def make_sincos(n_timesteps, n_pairs):
    """
    Generate a 2D array of sine and cosine pairs at random frequencies and
    linear phase offsets depending on position in minibatch.

    Used for simple testing of RNN algorithms.

    Parameters
    ----------
    n_timesteps : int
        number of timesteps

    n_pairs : int
        number of sine, cosine pairs to generate

    Returns
    -------
    pairs : array, shape (n_timesteps, n_pairs, 2)
        A minibatch of sine, cosine pairs with the RNN minibatch converntion
        (timestep, sample, feature).
    """
    n_timesteps = int(n_timesteps)
    n_pairs = int(n_pairs)
    random_state = np.random.RandomState(1999)
    frequencies = 5 * random_state.rand(n_pairs) + 1
    frequency_base = np.arange(n_timesteps) / (2 * np.pi)
    steps = frequency_base[:, None] * frequencies[None]
    phase_offset = np.arange(n_pairs) / (2 * np.pi)
    sines = np.sin(steps + phase_offset)
    cosines = np.sin(steps + phase_offset + np.pi / 2)
    sines = sines[:, :, None]
    cosines = cosines[:, :, None]
    pairs = np.concatenate((sines, cosines), axis=-1).astype(
        theano.config.floatX)
    return pairs


def load_iris():
    """
    Load and return the iris dataset (classification).

    This is basically the sklearn dataset loader, except returning a dictionary.

    n_samples : 150
    n_features : 4

    Returns
    -------
    summary : dict
        A dictionary cantaining data and target labels

        summary["data"] : array, shape (150, 4)
            The data for iris

        summary["target"] : array, shape (150,)
            The classification targets

    """
    module_path = os.path.dirname(__file__)
    with open(os.path.join(module_path, 'data', 'iris.csv')) as csv_file:
        data_file = csv.reader(csv_file)
        temp = next(data_file)
        n_samples = int(temp[0])
        n_features = int(temp[1])
        data = np.empty((n_samples, n_features), dtype=theano.config.floatX)
        target = np.empty((n_samples,), dtype=np.int32)

        for i, ir in enumerate(data_file):
            data[i] = np.asarray(ir[:-1], dtype=theano.config.floatX)
            target[i] = np.asarray(ir[-1], dtype=np.int32)

    return {"data": data, "target": target}


def load_digits():
    """
    Load and return the digits dataset (classification).

    This is basically the sklearn dataset loader, except returning a dictionary.

    n_samples : 1797
    n_features : 64

    Returns
    -------
    summary : dict
        A dictionary cantaining data and target labels

        summary["data"] : array, shape (1797, 64)
            The data for digits

        summary["target"] : array, shape (1797,)
            The classification targets

    """

    module_path = os.path.dirname(__file__)
    data = np.loadtxt(os.path.join(module_path, 'data', 'digits.csv.gz'),
                      delimiter=',')
    target = data[:, -1].astype("int32")
    flat_data = data[:, :-1].astype(theano.config.floatX)
    return {"data": flat_data, "target": target}
