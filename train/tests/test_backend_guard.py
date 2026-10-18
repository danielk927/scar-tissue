from types import SimpleNamespace

import pytest

from scar_train.errors import ApiError
from scar_train.loop import check_backend, run_generation
from scar_train.routes.loop import LoopRunRequest, run_loop
from scar_train.trainer import FakeDeployer, FakeTrainer


@pytest.mark.parametrize("trainer,deployer", [(FakeTrainer(), object()), (object(), FakeDeployer())])
def test_full_size_weight_updates_refuse_fake_backends_before_database_work(trainer, deployer):
    context = SimpleNamespace(trainer=trainer, deployer=deployer)
    with pytest.raises(ApiError, match="tiny-mode only"):
        run_generation(None, context, "D", 0, 1)
    check_backend(context, "D", True)
    check_backend(context, "B", False)
    check_backend(context, "C", False)


def test_route_refuses_fake_training_before_queueing_a_background_job():
    context = SimpleNamespace(trainer=FakeTrainer(), deployer=FakeDeployer())
    request = LoopRunRequest(arm="D", seed=0, generations=[1])
    with pytest.raises(ApiError, match="tiny-mode only"):
        run_loop(request, object(), context)
