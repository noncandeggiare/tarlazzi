import sqlite3
import json
import os
import logging
from datetime import datetime
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

class Database:
    def __init__(self, db_name='tarlazzi.db'):
        self.db_name = db_name
        self.users_file = 'users.json'
        self.init_db()
    
    def init_db(self):
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS gare (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    descrizione TEXT NOT NULL,
                    data_scadenza TIMESTAMP NOT NULL,
                    chat_id INTEGER NOT NULL,
                    message_id_recap INTEGER,
                    attiva INTEGER DEFAULT 1
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS puntate (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    gara_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    username TEXT NOT NULL,
                    ciclista1 TEXT NOT NULL,
                    ciclista2 TEXT NOT NULL,
                    ciclista3 TEXT NOT NULL,
                    FOREIGN KEY (gara_id) REFERENCES gare(id),
                    UNIQUE(gara_id, user_id)
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS utenti_gruppo (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT NOT NULL,
                    chat_id INTEGER NOT NULL
                )
            ''')
            
            conn.commit()
    
    def aggiungi_gara(self, descrizione: str, data_scadenza: datetime, chat_id: int) -> int:
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute(
                'INSERT INTO gare (descrizione, data_scadenza, chat_id) VALUES (?, ?, ?)',
                (descrizione, data_scadenza, chat_id)
            )
            conn.commit()
            return cursor.lastrowid
    
    def get_gare_attive(self, chat_id: int) -> List[Tuple]:
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT id, descrizione, data_scadenza FROM gare WHERE chat_id = ? AND attiva = 1 ORDER BY data_scadenza',
                (chat_id,)
            )
            return cursor.fetchall()
    
    def aggiungi_puntata(self, gara_id: int, user_id: int, username: str, ciclisti: List[str]):
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''INSERT OR REPLACE INTO puntate 
                   (gara_id, user_id, username, ciclista1, ciclista2, ciclista3) 
                   VALUES (?, ?, ?, ?, ?, ?)''',
                (gara_id, user_id, username, ciclisti[0], ciclisti[1], ciclisti[2])
            )
            conn.commit()
    
    def get_puntate_gara(self, gara_id: int) -> List[Tuple]:
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT username, ciclista1, ciclista2, ciclista3 FROM puntate WHERE gara_id = ? ORDER BY username',
                (gara_id,)
            )
            return cursor.fetchall()
    
    def get_user_ids_che_hanno_puntato(self, gara_id: int) -> List[int]:
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT user_id FROM puntate WHERE gara_id = ?',
                (gara_id,)
            )
            return [row[0] for row in cursor.fetchall()]
    
    def update_message_id(self, gara_id: int, message_id: int):
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute(
                'UPDATE gare SET message_id_recap = ? WHERE id = ?',
                (message_id, gara_id)
            )
            conn.commit()
    
    def get_gara(self, gara_id: int) -> Optional[Tuple]:
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT id, descrizione, data_scadenza, chat_id, message_id_recap FROM gare WHERE id = ?',
                (gara_id,)
            )
            return cursor.fetchone()
    
    def elimina_gara(self, gara_id: int):
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM puntate WHERE gara_id = ?', (gara_id,))
            cursor.execute('DELETE FROM gare WHERE id = ?', (gara_id,))
            conn.commit()
    
    def aggiungi_utente_gruppo(self, user_id: int, username: str, chat_id: int):
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute(
                'INSERT OR REPLACE INTO utenti_gruppo (user_id, username, chat_id) VALUES (?, ?, ?)',
                (user_id, username, chat_id)
            )
            conn.commit()
    
    def get_utenti_gruppo(self, chat_id: int) -> List[Tuple[int, str]]:
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT user_id, username FROM utenti_gruppo WHERE chat_id = ?',
                (chat_id,)
            )
            return cursor.fetchall()
    
    def get_tutti_ciclisti_gara(self, gara_id: int) -> List[str]:
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT ciclista1, ciclista2, ciclista3 FROM puntate WHERE gara_id = ?',
                (gara_id,)
            )
            ciclisti = []
            for puntata in cursor.fetchall():
                ciclisti.extend([c for c in puntata if c != 'X'])
            return ciclisti
    
    def load_users_from_file(self, chat_id: int):
        if not os.path.exists(self.users_file):
            logger.warning(f"File {self.users_file} non trovato")
            return
        
        with open(self.users_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        for user in data['users']:
            user_id = user['user_id']
            username = user['username'] if user['username'] else user['name']
            self.aggiungi_utente_gruppo(user_id, username, chat_id)
        
        logger.info(f"Caricati {len(data['users'])} utenti dal file")
