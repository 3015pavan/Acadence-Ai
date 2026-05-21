#!/usr/bin/env python3
"""Add source column to datasets table if it doesn't exist."""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from backend.database import engine
from sqlalchemy import text, inspect

def add_source_column():
    """Add source column to datasets table."""
    inspector = inspect(engine)
    columns = [col['name'] for col in inspector.get_columns('datasets')]
    
    if 'source' in columns:
        print('✓ source column already exists')
        return True
    
    try:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE datasets ADD COLUMN source VARCHAR(32) NOT NULL DEFAULT 'upload'"))
            conn.commit()
        print('✓ Added source column to datasets table')
        return True
    except Exception as e:
        print(f'✗ Error: {e}')
        return False

if __name__ == '__main__':
    success = add_source_column()
    sys.exit(0 if success else 1)
