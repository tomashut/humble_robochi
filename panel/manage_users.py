#!/usr/bin/env python3
"""Alta/actualizacion de usuarios humanos del panel.

Equivalente a mosquitto_passwd, pero para el login de personas (no
tiene nada que ver con las credenciales de Mosquitto -- ver
panel/README.md sobre la diferencia entre ambas).
"""

import argparse
import getpass
import json

from server import USERS_FILE, hash_password, load_users


def main():
    parser = argparse.ArgumentParser(description='Agrega o actualiza un usuario del panel.')
    parser.add_argument('username')
    args = parser.parse_args()

    password = getpass.getpass('Contrasena: ')
    confirm = getpass.getpass('Repetir contrasena: ')
    if password != confirm:
        print('Las contrasenas no coinciden, no se guardo nada.')
        return

    users = load_users()
    salt_hex, hash_hex = hash_password(password)
    users[args.username] = {'salt': salt_hex, 'hash': hash_hex}
    USERS_FILE.write_text(json.dumps(users, indent=2))
    print(f'Usuario "{args.username}" guardado en {USERS_FILE}.')


if __name__ == '__main__':
    main()
