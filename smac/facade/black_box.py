from __future__ import annotations

from typing import Any

import numpy as np
import sklearn.gaussian_process.kernels as kernels

from smac.acquisition import EI, AbstractAcquisitionFunction
from smac.acquisition.maximizer import (
    AbstractAcquisitionOptimizer,
    LocalAndSortedRandomSearch,
)
from smac.config import Config
from smac.configspace import Configuration
from smac.facade.algorithm_configuration import AlgorithmConfigurationFacade
from smac.initial_design.initial_design import InitialDesign
from smac.initial_design.sobol_design import SobolInitialDesign
from smac.intensification.intensification import Intensifier
from smac.model.configuration_chooser.random_chooser import ChooserProb, RandomChooser
from smac.model.gaussian_process import BaseModel, GaussianProcess
from smac.model.gaussian_process.kernels import (
    ConstantKernel,
    HammingKernel,
    Matern,
    WhiteKernel,
)
from smac.model.gaussian_process.mcmc import MCMCGaussianProcess
from smac.model.gaussian_process.utils.prior import HorseshoePrior, LognormalPrior
from smac.model.utils import get_types

__author__ = "Marius Lindauer"  # TODO leave author as is?
__copyright__ = "Copyright 2018, ML4AAD"
__license__ = "3-clause BSD"


class SMAC4BB(AlgorithmConfigurationFacade):
    """Facade to use SMAC for Black-Box optimization using a GP.

    see smac.facade.smac_Facade for API
    This facade overwrites options available via the SMAC facade

    Hyperparameters are chosen according to the best configuration for Gaussian process maximum likelihood found in
    "Towards Assessing the Impact of Bayesian Optimization's Own Hyperparameters" by Lindauer et al., presented at the
    DSO workshop 2019 (https://arxiv.org/abs/1908.06674).

    Changes are:

    * Instead of having an initial design of size 10*D as suggested by Jones et al. 1998 (actually, they suggested
      10*D+1), we use an initial design of 8*D.
    * More restrictive lower and upper bounds on the length scale for the Matern and Hamming Kernel than the ones
      suggested by Klein et al. 2017 in the RoBO package. In practice, they are ``np.exp(-6.754111155189306)``
      instead of ``np.exp(-10)`` for the lower bound and ``np.exp(0.0858637988771976)`` instead of
      ``np.exp(2)`` for the upper bound.
    * The initial design is set to be a Sobol grid
    * The random fraction is set to ``0.08447232371720552``, it was ``0.0`` before.

    See Also
    --------
    :class:`~smac.facade.smac_ac_facade.SMAC4AC` for documentation of parameters.

    Attributes
    ----------
    logger
    stats : Stats
    solver : SMBO
    runhistory : RunHistory
        List with information about previous runs
    trajectory : list
        List of all incumbents
    """

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)

        if len(self.config.instance_features) > 0:
            raise NotImplementedError("The Black-Box GP cannot handle instances.")

        # TODO what about these? vvv
        # self.solver.scenario.acq_opt_challengers = 1000  # type: ignore[attr-defined] # noqa F821
        # # activate predict incumbent
        # self.solver.epm_chooser.predict_x_best = True

    @staticmethod
    def get_model(config: Config, *, model_type: str = "gp", kernel: kernels.Kernel | None = None) -> BaseModel:
        available_model_types = ["gp", "gp_mcmc"]
        if model_type not in available_model_types:
            raise ValueError(f"model_type {model_type} not in available model types")

        if kernel is None:
            kernel = SMAC4BB.get_kernel(config=config)

        rng = np.random.default_rng(seed=config.seed)
        types, bounds = get_types(config.configspace, instance_features=None)
        if model_type == "gp":
            model = GaussianProcess(
                configspace=config.configspace,
                types=types,
                bounds=bounds,
                kernel=kernel,
                normalize_y=True,
                seed=rng.integers(low=0, high=2**20),
            )
        elif model_type == "gp_mcmc":
            n_mcmc_walkers = 3 * len(kernel.theta)
            if n_mcmc_walkers % 2 == 1:
                n_mcmc_walkers += 1

            model = MCMCGaussianProcess(
                configspace=config.configspace,
                types=types,
                bounds=bounds,
                kernel=kernel,
                n_mcmc_walkers=n_mcmc_walkers,
                # integrate_acquisition_function=True,  # TODO what happened to this argument?
                chain_length=250,
                burnin_steps=250,
                normalize_y=True,
                seed=rng.integers(low=0, high=2**20),
            )
        else:
            raise ValueError("Unknown model type %s" % model_type)

        return model

    @staticmethod
    def get_kernel(config: Config):
        rng = np.random.default_rng(seed=config.seed)

        types, bounds = get_types(config.configspace, instance_features=None)
        cont_dims = np.where(np.array(types) == 0)[0]
        cat_dims = np.where(np.array(types) != 0)[0]

        if (len(cont_dims) + len(cat_dims)) != len(config.configspace.get_hyperparameters()):
            raise ValueError(
                "The inferred number of continuous and categorical hyperparameters "
                "must equal the total number of hyperparameters. Got "
                f"{(len(cont_dims) + len(cat_dims))} != {len(config.configspace.get_hyperparameters())}."
            )

        # Constant Kernel
        cov_amp = ConstantKernel(
            2.0,
            constant_value_bounds=(np.exp(-10), np.exp(2)),
            prior=LognormalPrior(mean=0.0, sigma=1.0, rng=rng),  # TODO convert expected arg RandomState -> Generator
        )

        # Continuous / Categorical Kernels
        exp_kernel, ham_kernel = 0.0, 0.0
        if len(cont_dims) > 0:
            exp_kernel = Matern(
                np.ones([len(cont_dims)]),
                [(np.exp(-6.754111155189306), np.exp(0.0858637988771976)) for _ in range(len(cont_dims))],
                nu=2.5,
                operate_on=cont_dims,
            )
        if len(cat_dims) > 0:
            ham_kernel = HammingKernel(
                np.ones([len(cat_dims)]),
                [(np.exp(-6.754111155189306), np.exp(0.0858637988771976)) for _ in range(len(cat_dims))],
                operate_on=cat_dims,
            )

        # Noise Kernel
        noise_kernel = WhiteKernel(
            noise_level=1e-8,
            noise_level_bounds=(np.exp(-25), np.exp(2)),
            prior=HorseshoePrior(scale=0.1, rng=rng),
        )

        # Continuous and categecorical HPs
        if len(cont_dims) > 0 and len(cat_dims) > 0:
            kernel = cov_amp * (exp_kernel * ham_kernel) + noise_kernel

        # Only continuous HPs
        elif len(cont_dims) > 0 and len(cat_dims) == 0:
            kernel = cov_amp * exp_kernel + noise_kernel

        # Only categorical HPs
        elif len(cont_dims) == 0 and len(cat_dims) > 0:
            kernel = cov_amp * ham_kernel + noise_kernel

        else:
            raise ValueError("The number of continuous and categorical hyperparameters " "must be greater than zero.")

        return kernel

    @staticmethod
    def get_acquisition_function(config: Config, par: float = 0.0) -> AbstractAcquisitionFunction:
        return EI(par=par)

    @staticmethod
    def get_acquisition_optimizer(
        config: Config,
        acquisition_function: AbstractAcquisitionFunction,
        *,
        n_steps_plateau_walk: int = 10,
        n_sls_iterations: int = 10,
    ) -> AbstractAcquisitionOptimizer:
        optimizer = LocalAndSortedRandomSearch(
            acquisition_function,
            config_space=config.configspace,
            n_sls_iterations=n_sls_iterations,
            n_steps_plateau_walk=n_steps_plateau_walk,
            seed=config.seed,
        )
        return optimizer

    @staticmethod
    def get_intensifier(
        config: Config,
        *,
        adaptive_capping_slackfactor: float = 1.2,
        min_challenger: int = 1,
        min_config_calls: int = 1,
        max_config_calls: int = 2000,
    ) -> Intensifier:
        # only 1 configuration per SMBO iteration
        if config.deterministic:
            min_challenger = 1

        intensifier = Intensifier(
            instances=config.instances,
            instance_specifics=config.instance_specifics,  # What is that?
            algorithm_walltime_limit=config.algorithm_walltime_limit,
            deterministic=config.deterministic,
            adaptive_capping_slackfactor=adaptive_capping_slackfactor,
            min_challenger=min_challenger,
            race_against=config.configspace.get_default_configuration(),
            min_config_calls=min_config_calls,
            max_config_calls=max_config_calls,
            seed=config.seed,
        )

        return intensifier

    @staticmethod
    def get_initial_design(
        config: Config,
        *,
        initial_configs: list[Configuration] | None = None,
        n_configs_per_hyperparameter: int = 8,
        max_config_fracs: float = 0.25,
    ) -> InitialDesign:
        if len(config.configspace.get_hyperparameters()) > 21201:
            raise ValueError(
                'The default initial design "Sobol sequence" can only handle up to 21201 dimensions. '
                'Please use a different initial design, such as the "Latin Hypercube design".',
            )
        initial_design = SobolInitialDesign(
            configspace=config.configspace,
            n_runs=config.n_runs,
            configs=initial_configs,
            n_configs_per_hyperparameter=n_configs_per_hyperparameter,
            max_config_fracs=max_config_fracs,
            seed=config.seed,
        )
        return initial_design

    @staticmethod
    def get_random_configuration_chooser(
        config: Config, *, random_probability: float = 0.08447232371720552
    ) -> RandomChooser:
        return ChooserProb(rng=np.default_rng(seed=config.seed), prob=random_probability)