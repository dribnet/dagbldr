from collections import OrderedDict
import numpy as np
import theano

from dagbldr.datasets import fetch_binarized_mnist
from dagbldr.optimizers import adam
from dagbldr.utils import add_datasets_to_graph, get_params_and_grads
from dagbldr.utils import convert_to_one_hot
from dagbldr.utils import load_last_checkpoint, early_stopping_trainer
from dagbldr.nodes import softplus_layer, linear_layer, sigmoid_layer
from dagbldr.nodes import gaussian_log_sample_layer, gaussian_log_kl
from dagbldr.nodes import binary_crossentropy, softmax_layer
from dagbldr.nodes import categorical_crossentropy


mnist = fetch_binarized_mnist()
train_indices = mnist["train_indices"]
valid_indices = mnist["valid_indices"]
X = mnist["data"]
y = mnist["target"]
n_targets = 10
y = convert_to_one_hot(y, n_targets)

# graph holds information necessary to build layers from parents
graph = OrderedDict()
X_sym, y_sym = add_datasets_to_graph([X, y], ["X", "y"], graph)
# random state so script is deterministic
random_state = np.random.RandomState(1999)

minibatch_size = 100
n_code = 100
n_enc_layer = [200, 200, 200]
n_dec_layer = [200, 200]
width = 28
height = 28
n_input = width * height

# q(y_pred | x)
y_l1_enc = softplus_layer([X_sym], graph, 'y_l1_enc', n_enc_layer[0],
                          random_state)
y_pred = softmax_layer([y_l1_enc], graph, 'y_pred',  n_targets, random_state)

# partial q(z | x, y_pred)
X_l1_enc = softplus_layer([X_sym, y_pred], graph, 'X_l1_enc', n_enc_layer[1],
                          random_state)

# combined q(y_pred | x) and partial q(z | x) for q(z | x, y_pred)
l2_enc = softplus_layer([X_l1_enc], graph, 'l2_enc', n_enc_layer[2],
                        random_state)

# code layer
code_mu = linear_layer([l2_enc], graph, 'code_mu', n_code, random_state)
code_log_sigma = linear_layer([l2_enc], graph, 'code_log_sigma', n_code,
                              random_state)
kl = gaussian_log_kl([code_mu], [code_log_sigma], graph, 'kl').mean()
samp = gaussian_log_sample_layer([code_mu], [code_log_sigma], graph, 'samp',
                                 random_state)

# decode path aka p(x | z, y) for labeled data
l1_dec = softplus_layer([samp, y_sym], graph, 'l1_dec',  n_dec_layer[0],
                        random_state)
l2_dec = softplus_layer([l1_dec], graph, 'l2_dec', n_dec_layer[1], random_state)
out = sigmoid_layer([l2_dec], graph, 'out', n_input, random_state)

nll = binary_crossentropy(out, X_sym).mean()
# log p(x) = -nll so swap sign
# want to minimize cost in optimization so multiply by -1
base_cost = -1 * (-nll - kl)

# -log q(y | x) is negative log likelihood already
alpha = 0.1
err = categorical_crossentropy(y_pred, y_sym).mean()
cost = base_cost + alpha * err

params, grads = get_params_and_grads(graph, cost)

learning_rate = 0.0002
opt = adam(params)
updates = opt.updates(params, grads, learning_rate)

# Checkpointing
try:
    checkpoint_dict = load_last_checkpoint()
    fit_function = checkpoint_dict["fit_function"]
    cost_function = checkpoint_dict["cost_function"]
    predict_function = checkpoint_dict["predict_function"]
    encode_function = checkpoint_dict["encode_function"]
    decode_function = checkpoint_dict["decode_function"]
    previous_epoch_results = checkpoint_dict["previous_epoch_results"]
except KeyError:
    fit_function = theano.function([X_sym, y_sym], [nll, kl, nll + kl],
                                   updates=updates)
    cost_function = theano.function([X_sym, y_sym], [nll + kl])
    predict_function = theano.function([X_sym], [y_pred])
    encode_function = theano.function([X_sym], [code_mu, code_log_sigma])
    decode_function = theano.function([samp, y_sym], [out])
    checkpoint_dict = {}
    checkpoint_dict["fit_function"] = fit_function
    checkpoint_dict["cost_function"] = cost_function
    checkpoint_dict["predict_function"] = predict_function
    checkpoint_dict["encode_function"] = encode_function
    checkpoint_dict["decode_function"] = decode_function
    previous_epoch_results = None

epoch_results = early_stopping_trainer(
    fit_function, cost_function, checkpoint_dict, [X, y],
    minibatch_size, train_indices, valid_indices,
    fit_function_output_names=["nll", "kl", "lower_bound"],
    cost_function_output_name="valid_lower_bound",
    n_epochs=2000, previous_epoch_results=previous_epoch_results,
    shuffle=True, random_state=random_state)
