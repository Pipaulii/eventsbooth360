"""PostgreSQL adapter for the shared booking queries; SQLite remains local only."""
import re
class InsertResult:
    def __init__(self,ident):self.lastrowid=ident
class PostgresConnection:
    def __init__(self,url):
        import psycopg
        self.db=psycopg.connect(url,connect_timeout=10)
    def execute(self,sql,params=()):
        if sql=='BEGIN IMMEDIATE':return self.db.execute('SELECT pg_advisory_xact_lock(3602026)')
        sql=sql.replace('id INTEGER PRIMARY KEY','id BIGSERIAL PRIMARY KEY').replace('?','%s')
        # END is reserved in PostgreSQL; retain the shared SQLite column name.
        sql=re.sub(r'\bend\b', '"end"', sql)
        if sql.startswith('INSERT OR REPLACE INTO settings'):
            sql=sql.replace('INSERT OR REPLACE','INSERT')+' ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value'
        if sql.startswith('INSERT INTO slots('):
            return InsertResult(self.db.execute(sql+' RETURNING id',params).fetchone()[0])
        return self.db.execute(sql,params)
    def commit(self):self.db.commit()
    def close(self):self.db.close()
    def __enter__(self):return self
    def __exit__(self,kind,value,trace):
        try:
            if kind:self.db.rollback()
            else:self.db.commit()
        finally:self.close()
