CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Games table
CREATE TABLE IF NOT EXISTS games (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    game_id TEXT UNIQUE NOT NULL,
    course_name TEXT NOT NULL,
    game_date DATE NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Players table
CREATE TABLE IF NOT EXISTS players (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    game_id UUID REFERENCES games(id) ON DELETE CASCADE,
    name TEXT NOT NULL
);

-- Scores table
CREATE TABLE IF NOT EXISTS scores (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    game_id UUID REFERENCES games(id) ON DELETE CASCADE,
    player_id UUID REFERENCES players(id) ON DELETE CASCADE,
    hole_number INT CHECK (hole_number BETWEEN 1 AND 18),
    strokes INT CHECK (strokes BETWEEN 1 AND 8),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE (game_id, player_id, hole_number)
);
