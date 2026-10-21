"""The reference deployment answers on a threadpool over one catalog connection (plan V2.0 §5.7, §9.7).

A workbench screen fires several reads at once, and each sync route runs on a worker thread of the same
event loop while every thread shares one sqlite connection. Two consequences are invisible to a
single-threaded test client, so they are stated here: statements have to be serialised or the connection
deadlocks with both threads waiting on the other, and the session project scope belongs to the thread
that set it - otherwise one project's read can be answered under another project's scope.

"""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from bioadapt_policies.access import Role
from conftest import OTHER_PROJECT_ID, PI, PROJECT_ID, asset, other_asset

READ_PATHS = (
    f"/v1/projects/{PROJECT_ID}",
    f"/v1/projects/{PROJECT_ID}/assets",
    f"/v1/projects/{PROJECT_ID}/runs",
    f"/v1/projects/{PROJECT_ID}/candidates",
    f"/v1/projects/{PROJECT_ID}/approvals",
)


def test_parallel_reads_over_one_catalog_all_answer(client, auth) -> None:
    """The deadlock needs exactly two overlapping requests, so this fires a screen's worth of them."""
    headers = auth(PI, [Role.PRINCIPAL_INVESTIGATOR])
    paths = [path for _ in range(4) for path in READ_PATHS]

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(client.get, path, headers=headers) for path in paths]
        responses = [future.result(timeout=60) for future in futures]

    assert sorted({one.status_code for one in responses}) == [200], [
        (one.request.url.path, one.status_code, one.text[:200]) for one in responses
    ]


def test_a_write_and_a_read_never_share_one_transaction(client, auth, state) -> None:
    """A write and a read share one connection: the write must not land inside another thread's transaction."""
    headers = auth(PI, [Role.PRINCIPAL_INVESTIGATOR])

    def read() -> str:
        response = client.get(f"/v1/projects/{PROJECT_ID}/assets", headers=headers)
        return f"{response.status_code}"

    def write(round_: int) -> str:
        response = client.post(
            f"/v1/projects/{PROJECT_ID}/assets",
            headers=headers,
            json=asset(asset_id=f"as_conc_{round_}", size_bytes=64, file_sha256="2" * 64).model_dump(mode="json"),
        )
        return f"{response.status_code} {response.text[:160]}"

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(read) for _ in range(6)]
        futures += [pool.submit(write, round_) for round_ in range(4)]
        codes = [future.result(timeout=60) for future in futures]

    assert all(str(code).startswith(("200", "201")) for code in codes), codes


def test_project_scope_belongs_to_the_thread_that_set_it(catalog) -> None:
    """Two threads set different scopes before either reads, so a shared scope would answer for the wrong project."""
    catalog.set_scope(OTHER_PROJECT_ID)
    catalog.record_asset(other_asset())
    catalog.set_scope(PROJECT_ID)
    barrier = threading.Barrier(2)
    observed: dict[str, tuple[str, list[str]]] = {}
    failures: list[BaseException] = []

    def read(project_id: str) -> None:
        try:
            catalog.set_scope(project_id)
            barrier.wait(timeout=30)
            observed[project_id] = (catalog.scope, sorted(one.asset_id for one in catalog.list_assets()))
        except BaseException as exc:  # noqa: BLE001 - a thread that dies here has to fail the test
            failures.append(exc)

    threads = [threading.Thread(target=read, args=(pid,)) for pid in (PROJECT_ID, OTHER_PROJECT_ID)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not failures, failures
    assert observed[PROJECT_ID] == (PROJECT_ID, ["as_0001"])
    assert observed[OTHER_PROJECT_ID] == (OTHER_PROJECT_ID, ["as_shared"])


def test_scope_is_denied_on_a_thread_that_never_asked(catalog) -> None:
    """Failing closed is the contract: a thread without a scope gets no rows, not the previous project's."""
    from bioadapt_storage.catalog import ScopeError

    catalog.set_scope(PROJECT_ID)
    with pytest.raises(ScopeError):
        holder: list[BaseException] = []

        def unscoped() -> None:
            try:
                catalog.list_assets()
            except BaseException as exc:  # noqa: BLE001
                holder.append(exc)

        thread = threading.Thread(target=unscoped)
        thread.start()
        thread.join(timeout=30)
        raise holder[0]
