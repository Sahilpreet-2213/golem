import logging
import math
import os
from datetime import datetime
from hashlib import sha256
from typing import Optional, Tuple, Union
from _pysha3 import sha3_256 as _sha3_256

from golem_messages.cryptography import ECCx, mk_privkey, ecdsa_verify, \
    privtopub

from golem.core.variables import PRIVATE_KEY
from golem.utils import encode_hex, decode_hex
from .simpleenv import get_local_datadir

logger = logging.getLogger(__name__)


def sha3(seed: Union[str, bytes]) -> bytes:
    """ Return sha3-256 (NOT keccak) of seed in digest
    :param seed: data that should be hashed
    :return: binary hashed data
    """
    if isinstance(seed, str):
        seed = seed.encode()
    return _sha3_256(seed).digest()


def sha2(seed: Union[str, bytes]) -> int:
    if isinstance(seed, str):
        seed = seed.encode()
    return int.from_bytes(sha256(seed).digest(), 'big')


def get_random(min_value: int = 0, max_value: Optional[int] = None) -> int:
    """
    Get cryptographically secure random integer in range
    :param min_value: Minimal value
    :param max_value: Maximum value
    :return: Random number in range <min_value, max_value>
    """

    from Crypto.Random.random import randrange
    from sys import maxsize

    if max_value is None:
        max_value = maxsize
    if min_value > max_value:
        raise ArithmeticError("max_value should be greater than min_value")
    if min_value == max_value:
        return min_value
    return randrange(min_value, max_value)


def get_random_float() -> float:
    """
    Get random number in range (0, 1)
    :return: Random number in range (0, 1)
    """
    result = get_random(min_value=2)
    return float(result - 1) / float(10 ** len(str(result)))


class EllipticalKeysAuth:
    """
    Elliptical curves cryptographic authorization manager. Create and keeps
    private and public keys based on ECC (curve secp256k1).
    """
    PRIV_KEY_LEN = 32
    PUB_KEY_LEN = 64
    HEX_PUB_KEY_LEN = 128
    KEY_ID_LEN = 128

    _private_key_path = ""  # type: str
    _private_key = b''  # type: bytes
    public_key = b''  # type: bytes
    key_id = ""  # type: str
    ecc = None  # type: ECCx

    def __init__(
            self,
            datadir: str,
            private_key_name: str = PRIVATE_KEY,
            difficulty: int = 0) -> None:
        """
        Create new ECC keys authorization manager, load or create keys.

        :param datadir where to store files
        :param private_key_name: name of the file containing private key
        :param difficulty:
            desired key difficulty level.
            It's a number of leading zeros in binary representation of
            public key. Works with floats too.
            Value in range <0, 256>. 0 is not difficult.
            Maximum is impossible.
        """

        if not datadir:
            datadir = get_local_datadir('default')
        keys_dir = os.path.join(datadir, 'keys')
        if not os.path.isdir(keys_dir):
            os.makedirs(keys_dir)

        self.difficulty = difficulty
        self._private_key_path = os.path.join(keys_dir, private_key_name)

        loaded_keys = EllipticalKeysAuth._load_and_check_keys(
            self._private_key_path, difficulty)

        if loaded_keys:
            priv_key, pub_key = loaded_keys
        else:
            logger.info("Backing up existing keys and creating new key pair.")
            priv_key, pub_key = self._generate_new_keys(difficulty)

        self._setup_keys(priv_key, pub_key)

    @classmethod
    def get_keys_dir(cls, datadir=None):
        """ Path to the dir where keys files are stored."""
        if not hasattr(cls, '_keys_dir'):
            # TODO: Move keys to node's datadir.
            if datadir is None:
                datadir = get_local_datadir('default')
            cls._keys_dir = os.path.join(datadir, 'keys')
        if not os.path.isdir(cls._keys_dir):
            os.makedirs(cls._keys_dir)
        return cls._keys_dir

    @staticmethod
    def _load_and_check_keys(
            private_key_path: str,
            difficulty: int) -> Optional[Tuple[bytes, bytes]]:

        try:
            with open(private_key_path, 'rb') as f:
                priv_key = f.read()
        except FileNotFoundError:
            return None

        if not len(priv_key) == EllipticalKeysAuth.PRIV_KEY_LEN:
            logger.error("Unexpected private key size: %d. "
                         "Will create new keys.", len(priv_key))
            return None

        pub_key = privtopub(priv_key)

        if not EllipticalKeysAuth.is_pubkey_difficult(pub_key, difficulty):
            logger.warning("Current key is not difficult enough. "
                           "Will create new keys.")
            return None

        return priv_key, pub_key

    def _setup_keys(self, priv_key: bytes, pub_key: bytes) -> None:
        self._private_key = priv_key
        self.public_key = pub_key
        self.key_id = encode_hex(pub_key)
        self.ecc = ECCx(raw_privkey=priv_key)
        self._save_private_key()

    def _save_private_key(self):
        def backup_file(path):
            if os.path.exists(path):
                dirname, filename = os.path.split(path)
                date = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
                filename_bak = filename.replace('.', '_') + '_' + date + '.bak'
                os.rename(path, os.path.join(dirname, filename_bak))

        backup_file(self._private_key_path)
        with open(self._private_key_path, 'wb') as f:
            f.write(self._private_key)

    def encrypt(self, data: bytes, public_key: Optional[bytes] = None) -> bytes:
        """ Encrypt given data with ECIES
        :param data: data that should be encrypted
        :param public_key: *Default: None* public key that should be used to
                           encrypt data. Public key may be in digest (len == 64)
                           or hexdigest (len == 128).
        If public key is None then default public key will be used.
        :return: encrypted data
        """
        if public_key is None:
            public_key = self.public_key
        if len(public_key) == EllipticalKeysAuth.HEX_PUB_KEY_LEN:
            public_key = decode_hex(public_key)
        return ECCx.ecies_encrypt(data, public_key)

    def decrypt(self, data: bytes) -> bytes:
        """ Decrypt given data with ECIES
        :param data: encrypted data
        :return: decrypted data
        """
        return self.ecc.ecies_decrypt(data)

    def sign(self, data: bytes) -> bytes:
        """ Sign given data with ECDSA
        sha3 is used to shorten the data and speedup calculations
        :param data: data to be signed
        :return: signed data
        """
        return self.ecc.sign(data)

    def verify(self, sig: bytes, data: bytes,
               public_key: Optional[bytes] = None) -> bool:
        """
        Verify the validity of an ECDSA signature
        sha3 is used to shorten the data and speedup calculations
        :param sig: ECDSA signature
        :param data: expected data
        :param public_key: *Default: None* public key that should be used to
                           verify signed data.
        Public key may be in digest (len == 64) or hexdigest (len == 128).
        If public key is None then default public key will be used.
        :return bool: verification result
        """

        try:
            if public_key is None:
                public_key = self.public_key
            if len(public_key) == EllipticalKeysAuth.HEX_PUB_KEY_LEN:
                public_key = decode_hex(public_key)
            return ecdsa_verify(public_key, sig, data)
        except AssertionError:
            logger.info("Wrong key format")
        except Exception as exc:
            logger.error("Cannot verify signature: {}".format(exc))
        return False

    @staticmethod
    def _count_max_hash(difficulty: int) -> int:
        return 2 << (256 - difficulty - 1)

    @staticmethod
    def is_pubkey_difficult(pub_key: Union[bytes, str],
                            difficulty: int) -> bool:
        if isinstance(pub_key, str):
            pub_key = decode_hex(pub_key)
        return sha2(pub_key) < EllipticalKeysAuth._count_max_hash(difficulty)

    def is_difficult(self, difficulty: int) -> bool:
        return self.is_pubkey_difficult(self.public_key, difficulty)

    @staticmethod
    def _generate_new_keys(difficulty: int) -> Tuple[bytes, bytes]:
        while True:
            priv_key = mk_privkey(str(get_random_float()))
            pub_key = privtopub(priv_key)
            if EllipticalKeysAuth.is_pubkey_difficult(pub_key, difficulty):
                break
        return priv_key, pub_key

    def generate_new(self, difficulty: int) -> None:
        """ Generate new pair of keys with given difficulty

        :param difficulty: see __init__
        :raise TypeError: in case of incorrect @difficulty type
        """
        priv_key, pub_key = self._generate_new_keys(difficulty)
        self._setup_keys(priv_key, pub_key)

    def get_difficulty(self, key_id: Optional[str] = None) -> float:
        """
        Calculate key's difficulty.
        This is more expensive to calculate than is_difficult, so use
        the latter if you can.

        :param key_id: *Default: None* count difficulty of given key.
                       If key_id is None then use default key_id
        :return: key_id difficulty
        """
        pub_key = decode_hex(key_id) if key_id else self.public_key
        return 256 - math.log2(sha2(pub_key))
