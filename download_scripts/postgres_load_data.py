import pandas as pd
import numpy as np

import os
import sys
from pathlib import Path
import subprocess
import argparse
import logging

from sqlalchemy import create_engine

import data_helper as dh

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


def get_parser():
    """Args Description"""

    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)

    parser.add_argument("--data-dir", type=str, help="baseball data directory", default='../data')
    parser.add_argument("-v", "--verbose", help="verbose output", action="store_true")
    parser.add_argument("--log", dest="log_level", choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
                        help="Set the logging level")

    return parser


def psql(cmd, user='postgres', schema='baseball'):
    # For this to work without a password, a .pgpass file is necessary.
    # See: https://www.postgresql.org/docs/current/libpq-pgpass.html

    psql_cmd = ['psql', '-U', user, schema, '-c', cmd]
    p1 = subprocess.run(psql_cmd, shell=False, capture_output=True)
    logger.info(p1.stdout.decode('utf-8'))
    if p1.returncode != 0:
        logger.error(p1.stderr.decode('utf-8'))
        raise FileNotFoundError(f'{cmd} failed.')


def create_and_load_table(conn, prefix, filename, pkey=None):
    table = prefix + filename.name.split('.')[0]
    logger.info(f'{table} loading ...')

    # read optimized Pandas data types
    df = dh.from_csv_with_types(filename, nrows=0)

    # compute optimized database data types
    db_dtypes = dh.optimize_db_dtypes(df)

    """df.to_sql automatically creates a table with good but not optimal data types,
    hence the use of dh.optimize_db_dtypes()
    
    df.to_sql() will issue a commit per row.  This is extremely slow.  It is better
    to use the DBMS bulk load utility.
    """

    # drop then create an empty table
    conn.execute(f'DROP TABLE IF EXISTS {table} CASCADE')
    df.to_sql(table, conn, index=False, dtype=db_dtypes)

    # bulk load the data
    if filename.name.endswith('.gz'):
        cmd = f"\copy {table} from program 'zcat {filename.as_posix()}' CSV HEADER"
    else:
        cmd = f"\copy {table} from '{filename.as_posix()}' CSV HEADER"
    psql(cmd)

    # add primary key constraint
    if pkey:
        pkeys_str = ', '.join(pkey)
        sql = f'ALTER TABLE {table} ADD PRIMARY KEY ({pkeys_str})'
        conn.execute(sql)

    # rows added
    rs = conn.execute(f'SELECT COUNT(*) from {table}')
    result = rs.fetchall()
    rows = result[0][0]

    logger.info(f'{table} added with {rows} rows')


def load_lahman_tables(conn, data_dir):
    lahman_data = data_dir.joinpath('lahman/wrangled')

    create_and_load_table(conn, 'lahman_', lahman_data / 'people.csv', ['player_id'])
    sql = 'ALTER TABLE lahman_people ADD CONSTRAINT retro_player_unique UNIQUE (retro_id)'
    conn.execute(sql)

    create_and_load_table(conn, 'lahman_', lahman_data / 'batting.csv',
                          ['player_id', 'year_id', 'stint'])
    create_and_load_table(conn, 'lahman_', lahman_data / 'battingpost.csv',
                          ['player_id', 'year_id', 'round'])
    create_and_load_table(conn, 'lahman_', lahman_data / 'pitching.csv',
                          ['player_id', 'year_id', 'stint'])
    create_and_load_table(conn, 'lahman_', lahman_data / 'pitchingpost.csv',
                          ['player_id', 'year_id', 'round'])
    create_and_load_table(conn, 'lahman_', lahman_data / 'fielding.csv',
                          ['player_id', 'year_id', 'stint', 'pos'])
    create_and_load_table(conn, 'lahman_', lahman_data / 'fieldingpost.csv',
                          ['player_id', 'year_id', 'round', 'pos'])
    create_and_load_table(conn, 'lahman_', lahman_data / 'parks.csv',
                          ['park_key'])
    create_and_load_table(conn, 'lahman_', lahman_data / 'salaries.csv',
                          ['player_id', 'year_id', 'team_id'])
    create_and_load_table(conn, 'lahman_', lahman_data / 'teams.csv',
                          ['team_id', 'year_id'])
    sql = 'ALTER TABLE lahman_teams ADD CONSTRAINT retro_team_unique UNIQUE (team_id_retro, year_id)'
    conn.execute(sql)


def load_retrosheet_tables(conn, data_dir):
    retro_data = data_dir.joinpath('retrosheet/wrangled')

    create_and_load_table(conn, 'retro_', retro_data / 'batting.csv.gz',
                          ['player_id', 'game_id'])
    create_and_load_table(conn, 'retro_', retro_data / 'pitching.csv.gz',
                          ['player_id', 'game_id'])
    create_and_load_table(conn, 'retro_', retro_data / 'fielding.csv.gz',
                          ['player_id', 'game_id', 'pos'])

    create_and_load_table(conn, 'retro_', retro_data / 'game.csv.gz',
                          ['game_id'])
    create_and_load_table(conn, 'retro_', retro_data / 'team_game.csv.gz',
                          ['team_id', 'game_id'])


def main():
    """Load the data in Postgres.
    """
    parser = get_parser()
    args = parser.parse_args()

    if args.log_level:
        fh = logging.FileHandler('download.log')
        formatter = logging.Formatter('%(asctime)s:%(name)s:%(levelname)s: %(message)s')
        fh.setFormatter(formatter)
        fh.setLevel(args.log_level)
        logger.addHandler(fh)

    if args.verbose:
        # send INFO level logging to stdout
        sh = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter('%(asctime)s:%(name)s:%(levelname)s: %(message)s')
        sh.setFormatter(formatter)
        sh.setLevel(logging.INFO)
        logger.addHandler(sh)

    # Get the user and password from the environment (rather than hardcoding it)
    db_user = os.environ.get('DB_USER')
    db_pass = os.environ.get('DB_PASS')

    # avoid putting passwords directly in code
    connect_str = f'postgresql://{db_user}:{db_pass}@localhost:5432/baseball'

    conn = create_engine(connect_str)

    data_dir = Path('../data')
    load_lahman_tables(conn, data_dir)
    load_retrosheet_tables(conn, data_dir)

    logger.info('Finished')


if __name__ == '__main__':
    main()