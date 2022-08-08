from __future__ import annotations

from smac.configspace import Configuration
from smac.facade.random import RandomFacade
from smac.initial_design import InitialDesign
from smac.initial_design.random_configuration_design import RandomInitialDesign
from smac.intensification.hyperband import Hyperband
from smac.scenario import Scenario

__author__ = "Ashwin Raaghav Narayanan"
__copyright__ = "Copyright 2019, ML4AAD"
__license__ = "3-clause BSD"


class HyperbandFacade(RandomFacade):
    """
    Facade to use model-free Hyperband [1]_ for algorithm configuration.

    Use ROAR (Random Aggressive Online Racing) to compare configurations, a random
    initial design and the Hyperband intensifier.

    .. [1] Lisha Li, Kevin G. Jamieson, Giulia DeSalvo, Afshin Rostamizadeh, Ameet Talwalkar:
        Hyperband: A Novel Bandit-Based Approach to Hyperparameter Optimization.
        J. Mach. Learn. Res. 18: 185:1-185:52 (2017)
        https://jmlr.org/papers/v18/16-558.html

    """

    @staticmethod
    def get_initial_design(
        scenario: Scenario,
        *,
        configs: list[Configuration] | None = None,
        n_configs: int | None = None,
        n_configs_per_hyperparamter: int = 10,
        max_config_ratio: float = 0.25,  # Use at most X*budget in the initial design
    ) -> RandomInitialDesign:
        return RandomInitialDesign(
            scenario=scenario,
            configs=configs,
            n_configs=n_configs,
            n_configs_per_hyperparameter=n_configs_per_hyperparamter,
            max_config_ratio=max_config_ratio,
        )

    @staticmethod
    def get_intensifier(
        scenario: Scenario,
        *,
        eta: int = 3,
        min_challenger: int = 1,
    ) -> Hyperband:
        return Hyperband(
            scenario=scenario,
            eta=eta,
            min_challenger=min_challenger,
        )
