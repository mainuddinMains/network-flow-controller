"""Unit tests for src/simulator/."""


def test_simulator_module_imports():
    """Verify the simulator module is importable."""
    import src.simulator  # noqa: F401


def operate_router(router_config_path):
    from src.simulator import router_builder

    router = router_builder.build_router_from_ini(router_config_path)
    router.operation_loop()

def test_network():
    import time
    from multiprocessing import Process
    from pathlib import Path

    from src.simulator import router_builder

    "1.) Get router configuration files"
    root = Path.cwd().parent
    router_config = root / "src" / "simulator" / "router configurations"
    host_config = root / "src" / "simulator" / "host configurations"

    "2.) Get router paths"
    router_A = (router_config / "routerA.ini")
    router_B = (router_config / "routerB.ini")
    router_C = (router_config / "routerC.ini")
    router_D = (router_config / "routerD.ini")

    "3.) Get host paths"
    host_A = (host_config / "hostA.ini")
    host_B = (host_config / "hostB.ini")

    "4.) Create host programs."

    "5.) Create router programs."
    pA = Process(target=operate_router, args=(router_A,),)
    pB = Process(target=operate_router, args=(router_B,),)
    pC = Process(target=operate_router, args=(router_C,),)
    pD = Process(target=operate_router, args=(router_D,),)

    pA.start()
    pB.start()
    pC.start()
    pD.start()


    time.sleep(2.5)

    data = local_router_start._create_datagram(
        TTL=100,
        type_flags=0,
        reserve=0,
        identifier=1,
        source=local_router_start.virtual_address,
        destination=5,
        payload="Test payload."
    )

    # As the start router is controlled manually, it will not log being ready
    # to receive a packet.
    local_router_start._send_datagram(datagram=data, end_destination=5)

    time.sleep(2.5)

    "There may be a ConnectionResetError as the threads are closed."
    pB.terminate()
    pC.terminate()
    pD.terminate()
    pE.terminate()

    pB.join()
    pC.join()
    pD.join()
    pE.join()
