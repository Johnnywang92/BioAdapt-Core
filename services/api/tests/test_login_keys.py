"""Key files on disk, which is the half of §11.2 a person actually holds.

The verifier's refusals are asserted in ``test_iam_routes.py``; these tests are about the file plane -
that a saved key stays the same key across a restart, that the public half and its subject travel
together, and that the private half never becomes world-readable on the way.
"""

from stat import filemode

from bioadapt_api.login_keys import (
    generate_login_key,
    key_from_private,
    load_or_create_key_file,
    sidecar_path,
    subject_of_key_file,
)

SUBJECT = "user_platform_admin_01"
LABEL = "written by scripts/serve_workbench.py"


def seed(tmp_path):
    """The deployment's bootstrap write: a file that did not exist, in the directory it is pointed at."""
    path = tmp_path / "admin.pem"
    return path, load_or_create_key_file(path, subject=SUBJECT, label=LABEL)


class TestKeyFile:
    def test_a_saved_key_is_the_same_key_the_next_time_the_platform_boots(self, tmp_path):
        path, first = seed(tmp_path)
        again = load_or_create_key_file(path, subject=SUBJECT, label=LABEL)
        #: A restart that minted a fresh pair would quietly retire the file the person already copied,
        #: so the identifier has to come out of the bytes on disk rather than out of a new key.
        assert again.kid == first.kid
        assert again.fingerprint == first.fingerprint
        assert subject_of_key_file(path) == SUBJECT

    def test_the_private_half_is_owner_only_and_the_public_half_is_the_text_an_admin_registers(self, tmp_path):
        path, key = seed(tmp_path)
        assert filemode(path.stat().st_mode)[-9:] == "rw-------"
        side = sidecar_path(path)
        assert side.read_text(encoding="utf-8").startswith("-----BEGIN PUBLIC KEY-----")
        assert key.public_pem in side.read_text(encoding="utf-8")
        #: The sidecar states what a login would otherwise have to ask for, and nothing secret.
        assert f"subject={SUBJECT}" in side.read_text(encoding="utf-8")
        assert key.private_pem not in side.read_text(encoding="utf-8")

    def test_a_key_file_a_person_copied_in_gets_its_sidecar_written_for_it(self, tmp_path):
        """The upgrade case: the file exists from an earlier seed that wrote only the private half."""
        path = tmp_path / "pi.pem"
        key = generate_login_key()
        path.write_text(key.private_pem, encoding="utf-8")
        path.chmod(0o600)
        assert subject_of_key_file(path) == ""
        loaded = load_or_create_key_file(path, subject="user_pi_02", label="laptop")
        assert loaded.kid == key.kid
        assert subject_of_key_file(path) == "user_pi_02"
        #: Repairing the sidecar must not rewrite the credential itself - the bytes a person holds stay
        #: exactly the bytes they copied, permissions included.
        assert path.read_text(encoding="utf-8") == key.private_pem
        assert filemode(path.stat().st_mode)[-9:] == "rw-------"

    def test_everything_a_login_needs_is_derived_from_the_file(self, tmp_path):
        _path, key = seed(tmp_path)
        read_back = key_from_private(key.private_pem)
        assert (read_back.kid, read_back.fingerprint, read_back.public_pem) == (
            key.kid,
            key.fingerprint,
            key.public_pem,
        )
