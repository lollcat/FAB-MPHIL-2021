import torch
import torch.nn.functional as F
import torch.nn as nn
from TargetDistributions.base import BaseTargetDistribution
import copy


class NN(nn.Module):
    """Instantiate a neural net with weights, w ~ prior N(w; 0,1) """
    def __init__(self, x_dim=2, y_dim=2, n_hidden_layers=2, layer_width=10, simple_model=False):
        super(NN, self).__init__()
        self.x_dim = x_dim
        self.y_dim = y_dim
        self.hidden_layers = nn.ModuleList()
        in_dim = x_dim
        for i in range(n_hidden_layers):
            hidden_layer = nn.Linear(in_features=in_dim, out_features=layer_width)
            nn.init.normal_(hidden_layer.weight, mean=0.0, std=1.0)
            self.hidden_layers.append(hidden_layer)
            in_dim = layer_width

        if simple_model is False:
            self.output_layer_means = nn.Linear(in_dim, y_dim)
            self.output_layer_log_stds = nn.Linear(in_dim, y_dim)
            nn.init.normal_(self.output_layer_means.weight, mean=0.0, std=1.0)
            nn.init.normal_(self.output_layer_log_stds.weight, mean=0.0, std=1.0)
        if simple_model is True: # for 2D visualisation we want less param
            self.output_layer_means = nn.Linear(in_dim, y_dim, bias=False)
            self.output_layer_log_stds = nn.Linear(in_dim, y_dim, bias=False)
            nn.init.normal_(self.output_layer_means.weight, mean=0.0, std=1.0)
            nn.init.ones_(self.output_layer_log_stds.weight)
            self.output_layer_log_stds.requires_grad = False

    def forward(self, x):
        return self.sample_posterior_y_given_x(x)

    def sample_posterior_y_given_x(self, x):
        posterior_distribution = self.posterior_y_given_x(x)
        return posterior_distribution.sample()

    def posterior_y_given_x_log_prob(self, y, x):
        posterior_distribution = self.posterior_y_given_x(x)
        if self.y_dim > 1:
            return torch.sum(posterior_distribution.log_prob(y), dim=-1)
        else:
            return posterior_distribution.log_prob(y)

    def posterior_y_given_x(self, x):
        for hidden_layer in self.hidden_layers:
            x = F.elu(hidden_layer(x))
        means = F.leaky_relu(self.output_layer_means(x))
        # reparameterise to keep std resonably high, so that we have resonable density of most of y
        log_std = F.leaky_relu(self.output_layer_log_stds(x)) + 1
        stds = torch.exp(log_std)
        return torch.distributions.normal.Normal(loc=means, scale=stds)

    def set_parameters(self, flat_parameter_tensor):
        assert flat_parameter_tensor.numel() == self.n_parameters
        new_state_dict = {}
        param_counter = 0
        # keys = list(dict(model.state_dict()).keys())
        # key = keys[1]
        new_state_dict = self.state_dict()
        for name, parameter in self.named_parameters():
            if parameter.requires_grad:
                n_param = parameter.numel()
                tensor = flat_parameter_tensor[param_counter:n_param + param_counter]
                tensor = tensor.reshape(parameter.shape)
                new_state_dict[name] = tensor
                param_counter += n_param
        self.load_state_dict(new_state_dict)

    @property
    def n_parameters(self):
        return sum([tensor.numel() for tensor in self.parameters() if tensor.requires_grad])


class Target:
    """y = f_theta(x) where theta ~ N(0,1) (theta sampled once during initialisation), and x ~ N(0,1)
    Thus a target dataset is generated by sampling x and computing y
    The goal is to use this provide x & y data that we can use to get the posterior over weights of a BNN
    """
    def __init__(self, x_dim=2, y_dim=2, n_hidden_layers=2, layer_width=10, simple_mode=False):
        self.model = NN(x_dim, y_dim, n_hidden_layers, layer_width, simple_model=simple_mode)
        self.prior = torch.distributions.multivariate_normal.MultivariateNormal(loc=torch.zeros(x_dim),
                                                                                covariance_matrix=torch.eye(x_dim))

    def sample(self, n_points=100):
        x = self.prior.sample((n_points,))
        y = self.model(x)
        return x, y

class PosteriorBNN(BaseTargetDistribution):
    """
     p(w | X, Y) proportional to p(w) p(Y | X, w)
     where we generate X, Y datasets using the Target class
     if we set n_hidden_layer=0, x_dim=1, y_dim=1 we should be able to visualise p(w | X, Y) in 3D
    """
    def __init__(self, n_datapoints=100, x_dim=2, y_dim=2, n_hidden_layers=1, layer_width=5, simple_mode=False):
        super(PosteriorBNN, self).__init__()
        self.model = NN(x_dim, y_dim, n_hidden_layers, layer_width, simple_model=simple_mode)
        self.n_parameters = self.model.n_parameters
        self.prior_w = torch.distributions.multivariate_normal.MultivariateNormal(
            loc=torch.zeros(self.n_parameters), covariance_matrix=torch.eye(self.n_parameters))
        self.target = Target(x_dim, y_dim, n_hidden_layers, layer_width, simple_mode=simple_mode)
        self.X, self.Y = self.target.sample(n_datapoints)

    def log_prob(self, w):
        if len(w.shape) > 1:
            log_probs = list(map(self.log_prob_single_w, torch.split(w, 1, dim=0)))
            return torch.stack(log_probs)
        else:
            return self.log_prob_single_w(w)

    def log_prob_single_w(self, w):
        """p(w | X, Y) proportional to p(w) p(Y | X, w)"""
        w = torch.squeeze(w)
        model = copy.deepcopy(self.model)
        model.set_parameters(w)
        # keys = list(dict(model.state_dict()).keys())
        # key = keys[1]
        # model.state_dict()[key] == self.model.state_dict()[key] # want these to be false
        log_p_x = self.prior_w.log_prob(w)
        log_p_Y_given_w_X = torch.sum(model.posterior_y_given_x_log_prob(self.Y, self.X))
        return log_p_x + log_p_Y_given_w_X



if __name__ == '__main__':
    posterior_bnn = PosteriorBNN(n_datapoints=10, x_dim=2, y_dim=2, n_hidden_layers=1, layer_width=3)
    for _ in range(5):
        samples_w = torch.randn(posterior_bnn.model.n_parameters)
        print(posterior_bnn.log_prob(samples_w))

    posterior_bnn = PosteriorBNN(n_datapoints=10, x_dim=1, y_dim=1, n_hidden_layers=0, layer_width=0, simple_mode=True)
    from Utils import plot_distribution
    import matplotlib.pyplot as plt
    plot_distribution(posterior_bnn, n_points=300, range=4)
    plt.show()




