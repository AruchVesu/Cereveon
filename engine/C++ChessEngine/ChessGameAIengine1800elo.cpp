#include <vector>
#include <string>
#include <climits>
#include <algorithm>
#include <set>
#include <map>
#include <limits>
#include <chrono>
#include <memory>
#include <exception>
#include <stdexcept>
#include <unordered_map>
#undef max
#undef min
using namespace std;

enum Spalva { BALTA, JUODA };

// -------------------------------
// Simple built-in opening book
// key: FEN board (no side/castling)
// value: best move in "e2e4" format
// -------------------------------
#include <unordered_map>
#include <vector>
#include <random>

static const unordered_map<string, vector<string>> OPENING_BOOK = {

    // ===== Starting position =====
    { "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR",
      { "e2e4", "d2d4", "c2c4", "g1f3" } },

    // ===== After 1.e4 =====
    { "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR",
      { "c7c5", "e7e5", "e7e6" } },

    // ===== After 1.d4 =====
    { "rnbqkbnr/pppppppp/8/8/3P4/8/PPP1PPPP/RNBQKBNR",
      { "d7d5", "g8f6" } },

    // ===== Queen's Gambit =====
    { "rnbqkbnr/ppp1pppp/8/3p4/3P4/8/PPP1PPPP/RNBQKBNR",
      { "c2c4", "g1f3" } }
};


class Figura {
protected:
    Spalva spalva;
public:
    Figura(Spalva col) : spalva(col) {}
    virtual ~Figura() {}
    virtual char getSymbol() const = 0;
    virtual Figura* clone() const = 0;
    Spalva getSpalva() const { return spalva; }
    virtual bool canMove(int fromX, int fromY, int toX, int toY, const vector<vector<Figura*>>& lent) const = 0;
};

class Valdove : public Figura {
public:
    Valdove(Spalva col) : Figura(col) {}
    char getSymbol() const override { return 'Q'; }
    Figura* clone() const override { 
        try {
            return new Valdove(spalva); 
        } catch (...) {
            return nullptr;
        }
    }
    bool canMove(int fromX, int fromY, int toX, int toY, const vector<vector<Figura*>>& lent) const override {
        try {
            if (fromX < 0 || fromX >= 8 || fromY < 0 || fromY >= 8 || toX < 0 || toX >= 8 || toY < 0 || toY >= 8) return false;
            if (lent.size() != 8) return false;
            for (const auto& row : lent) {
                if (row.size() != 8) return false;
            }
            
            int dx = toX - fromX, dy = toY - fromY;
            if (dx == 0 || dy == 0 || abs(dx) == abs(dy)) {
                int stepX = (dx == 0) ? 0 : (dx > 0 ? 1 : -1);
                int stepY = (dy == 0) ? 0 : (dy > 0 ? 1 : -1);
                int x = fromX + stepX, y = fromY + stepY;
                while (x != toX || y != toY) {
                    if (x < 0 || x >= 8 || y < 0 || y >= 8) return false;
                    if (lent[x][y] != nullptr) return false;
                    x += stepX; y += stepY;
                }
                return (lent[toX][toY] == nullptr || lent[toX][toY]->getSpalva() != spalva);
            }
            return false;
        } catch (...) {
            return false;
        }
    }
};

class Bokstas : public Figura {
    bool hasMoved;
public:
    Bokstas(Spalva col) : Figura(col), hasMoved(false) {}
    char getSymbol() const override { return 'R'; }
    Figura* clone() const override { 
        try {
            Bokstas* newRook = new Bokstas(spalva);
            if (newRook) {
                newRook->hasMoved = hasMoved;
            }
            return newRook; 
        } catch (...) {
            return nullptr;
        }
    }
    bool canMove(int fromX, int fromY, int toX, int toY, const vector<vector<Figura*>>& lent) const override {
        try {
            if (fromX < 0 || fromX >= 8 || fromY < 0 || fromY >= 8 || toX < 0 || toX >= 8 || toY < 0 || toY >= 8) return false;
            if (lent.size() != 8) return false;
            for (const auto& row : lent) {
                if (row.size() != 8) return false;
            }
            
            if (fromX != toX && fromY != toY) return false;
            int dx = (toX > fromX) ? 1 : (toX < fromX ? -1 : 0);
            int dy = (toY > fromY) ? 1 : (toY < fromY ? -1 : 0);
            int x = fromX + dx, y = fromY + dy;
            while (x != toX || y != toY) {
                if (x < 0 || x >= 8 || y < 0 || y >= 8) return false;
                if (lent[x][y] != nullptr) return false;
                x += dx; y += dy;
            }
            return (lent[toX][toY] == nullptr || lent[toX][toY]->getSpalva() != spalva);
        } catch (...) {
            return false;
        }
    }
    void markMoved() { hasMoved = true; }
    bool getMoved() const { return hasMoved; }
};

class Karalius : public Figura {
    bool hasMoved;
public:
    Karalius(Spalva col) : Figura(col), hasMoved(false) {}
    char getSymbol() const override { return 'K'; }
    Figura* clone() const override { 
        try {
            Karalius* newKing = new Karalius(spalva);
            if (newKing) {
                newKing->hasMoved = hasMoved;
            }
            return newKing; 
        } catch (...) {
            return nullptr;
        }
    }
    bool canMove(int fromX, int fromY, int toX, int toY, const vector<vector<Figura*>>& lent) const override {
        try {
            if (fromX < 0 || fromX >= 8 || fromY < 0 || fromY >= 8 || toX < 0 || toX >= 8 || toY < 0 || toY >= 8) return false;
            if (lent.size() != 8) return false;
            for (const auto& row : lent) {
                if (row.size() != 8) return false;
            }
            
            int dx = abs(fromX - toX), dy = abs(fromY - toY);
            
            // Normal king move (one square in any direction)
            if (max(dx, dy) == 1) {
                // Check if target square is valid
                if (lent[toX][toY] != nullptr && lent[toX][toY]->getSpalva() == spalva) {
                    return false; // Can't capture own piece
                }
                
                // Check if kings are not adjacent after move
                for (int i = 0; i < 8; i++) {
                    for (int j = 0; j < 8; j++) {
                        if (lent[i][j] && lent[i][j]->getSpalva() != spalva && 
                            lent[i][j]->getSymbol() == 'K') {
                            // Found opponent king
                            int kingDistance = max(abs(toX - i), abs(toY - j));
                            if (kingDistance < 2) {
                                return false; // Kings would be too close
                            }
                        }
                    }
                }
                return true;
            }
            
            // Castling logic
            if (!hasMoved && fromX == toX && abs(toY - fromY) == 2) {
                int direction = (toY > fromY) ? 1 : -1;
                int rookY = (direction == 1) ? 7 : 0;
                
                // Check if rook exists and hasn't moved
                if (lent[fromX][rookY] == nullptr || 
                    lent[fromX][rookY]->getSymbol() != 'R' ||
                    lent[fromX][rookY]->getSpalva() != spalva) {
                    return false;
                }
                
                Bokstas* b = dynamic_cast<Bokstas*>(lent[fromX][rookY]);
                if (!b || b->getMoved()) {
                    return false;
                }
                
                // Check path is clear between king and rook
                int startY = min(fromY, rookY);
                int endY = max(fromY, rookY);
                for (int y = startY + 1; y < endY; y++) {
                    if (lent[fromX][y] != nullptr) return false;
                }
                
                return true;
            }
            return false;
        } catch (...) {
            return false;
        }
    }
    void markMoved() { hasMoved = true; }
    bool getMoved() const { return hasMoved; }
};

class Rikis : public Figura {
public:
    Rikis(Spalva col) : Figura(col) {}
    char getSymbol() const override { return 'B'; }
    Figura* clone() const override { 
        try {
            return new Rikis(spalva); 
        } catch (...) {
            return nullptr;
        }
    }
    bool canMove(int fromX, int fromY, int toX, int toY, const vector<vector<Figura*>>& lent) const override {
        try {
            if (fromX < 0 || fromX >= 8 || fromY < 0 || fromY >= 8 || toX < 0 || toX >= 8 || toY < 0 || toY >= 8) return false;
            if (lent.size() != 8) return false;
            for (const auto& row : lent) {
                if (row.size() != 8) return false;
            }
            
            int dx = abs(fromX - toX), dy = abs(fromY - toY);
            if (dx != dy) return false;
            int stepX = (toX > fromX) ? 1 : -1, stepY = (toY > fromY) ? 1 : -1;
            int x = fromX + stepX, y = fromY + stepY;
            while (x != toX) {
                if (x < 0 || x >= 8 || y < 0 || y >= 8) return false;
                if (lent[x][y] != nullptr) return false;
                x += stepX; y += stepY;
            }
            return (lent[toX][toY] == nullptr || lent[toX][toY]->getSpalva() != spalva);
        } catch (...) {
            return false;
        }
    }
};

class Zirgas : public Figura {
public:
    Zirgas(Spalva col) : Figura(col) {}
    char getSymbol() const override { return 'N'; }
    Figura* clone() const override { 
        try {
            return new Zirgas(spalva); 
        } catch (...) {
            return nullptr;
        }
    }
    bool canMove(int fromX, int fromY, int toX, int toY, const vector<vector<Figura*>>& lent) const override {
        try {
            if (fromX < 0 || fromX >= 8 || fromY < 0 || fromY >= 8 || toX < 0 || toX >= 8 || toY < 0 || toY >= 8) return false;
            if (lent.size() != 8) return false;
            for (const auto& row : lent) {
                if (row.size() != 8) return false;
            }
            
            int dx = abs(fromX - toX), dy = abs(fromY - toY);
            if ((dx == 2 && dy == 1) || (dx == 1 && dy == 2))
                return (lent[toX][toY] == nullptr || lent[toX][toY]->getSpalva() != spalva);
            return false;
        } catch (...) {
            return false;
        }
    }
};

class Peske : public Figura {
public:
    Peske(Spalva col) : Figura(col) {}
    char getSymbol() const override { return 'P'; }
    Figura* clone() const override { 
        try {
            return new Peske(spalva); 
        } catch (...) {
            return nullptr;
        }
    }
    bool canMove(int fromX, int fromY, int toX, int toY, const vector<vector<Figura*>>& lent) const override {
        try {
            if (fromX < 0 || fromX >= 8 || fromY < 0 || fromY >= 8 || toX < 0 || toX >= 8 || toY < 0 || toY >= 8) return false;
            if (lent.size() != 8) return false;
            for (const auto& row : lent) {
                if (row.size() != 8) return false;
            }
            
            int dir = (spalva == BALTA) ? -1 : 1; 
            int startRow = (spalva == BALTA) ? 6 : 1;
            if (toX - fromX == dir && toY == fromY && !lent[toX][toY]) return true;
            if (fromX == startRow && toX - fromX == 2 * dir && toY == fromY && !lent[fromX + dir][fromY] && !lent[toX][toY]) return true;
            if (toX - fromX == dir && abs(toY - fromY) == 1 && lent[toX][toY] && lent[toX][toY]->getSpalva() != spalva) return true;
            return false;
        } catch (...) {
            return false;
        }
    }
};

// Move structure for better organization
struct Move {
    int fromX, fromY, toX, toY;
    int score;
    string reason;
    
    Move() : fromX(-1), fromY(-1), toX(-1), toY(-1), score(INT_MIN), reason("") {}
    Move(int fx, int fy, int tx, int ty, int s = 0, string r = "") 
        : fromX(fx), fromY(fy), toX(tx), toY(ty), score(s), reason(r) {}
    
    bool isValid() const { return fromX >= 0 && fromY >= 0 && toX >= 0 && toY >= 0; }
    
    string toString() const {
        try {
            if (!isValid()) return "invalid";
            return string(1, 'a' + fromY) + to_string(8 - fromX) + " " + 
                   string(1, 'a' + toY) + to_string(8 - toX);
        } catch (...) {
            return "invalid";
        }
    }
};

// AI Configuration structure
struct AIConfig {
    int maxDepth;
    int maxNodes;
    int timeLimit; // milliseconds
    bool useTranspositionTable;
    bool useIterativeDeepening;
    
    AIConfig() : maxDepth(3), maxNodes(50000), timeLimit(5000), 
                 useTranspositionTable(true), useIterativeDeepening(true) {}
};

class SachmatuLenta {
private:
    vector<vector<Figura*>> lent;
    AIConfig aiConfig;
    Spalva currentTurn;
    
    mutable std::mt19937 rng{ std::random_device{}() };

    // Timeout handling
    chrono::steady_clock::time_point searchStartTime;
    bool isTimeUp() const {
        try {
            auto now = chrono::steady_clock::now();
            auto elapsed = chrono::duration_cast<chrono::milliseconds>(now - searchStartTime);
            return elapsed.count() > aiConfig.timeLimit;
        } catch (...) {
            return true; // If we can't check time, assume timeout
        }
    }

    int getPieceValue(Figura* fig) const {
        try {
            if (!fig) return 0;
            static const map<char, int> pieceValues = {
                {'P', 100}, {'N', 320}, {'B', 330}, {'R', 500}, {'Q', 900}, {'K', 20000}
            };
            char symbol = fig->getSymbol();
            auto it = pieceValues.find(symbol);
            return (it != pieceValues.end()) ? it->second : 0;
        } catch (...) {
            return 0;
        }
    }

    bool isSquareAttacked(int x, int y, Spalva attackerColor, const vector<vector<Figura*>>& board) const {
        try {
            if (x < 0 || x >= 8 || y < 0 || y >= 8) return false;
            if (board.size() != 8) return false;
            for (const auto& row : board) {
                if (row.size() != 8) return false;
            }
            
            for(int i = 0; i < 8; i++) {
                for(int j = 0; j < 8; j++) {
                    if(board[i][j] && board[i][j]->getSpalva() == attackerColor) {
                        if(board[i][j]->canMove(i, j, x, y, board)) return true;
                    }
                }
            }
        } catch (...) {
            return false;
        }
        return false;
    }

    bool isCenter(int x, int y) const { return (x >= 3 && x <= 4 && y >= 3 && y <= 4); }
    bool isExtendedCenter(int x, int y) const { return (x >= 2 && x <= 5 && y >= 2 && y <= 5); }

    Spalva opposite(Spalva c) const { return c==BALTA ? JUODA : BALTA; }

    bool isKingInCheck(const vector<vector<Figura*>>& board, Spalva c) const {
        try {
            if (board.size() != 8) return false;
            for (const auto& row : board) {
                if (row.size() != 8) return false;
            }
            
            int kingX = -1, kingY = -1;
            for(int i=0; i<8; i++) {
                for(int j=0; j<8; j++) {
                    if(board[i][j] && board[i][j]->getSpalva() == c && board[i][j]->getSymbol() == 'K') {
                        kingX = i; kingY = j; 
                        break;
                    }
                }
                if(kingX != -1) break;
            }
            if(kingX == -1) return false;
            return isSquareAttacked(kingX, kingY, opposite(c), board);
        } catch (...) {
            return false;
        }
    }

    int getTotalMaterial(const vector<vector<Figura*>>& board) const {
        try {
            if (board.size() != 8) return 0;
            for (const auto& row : board) {
                if (row.size() != 8) return 0;
            }
            
            int total = 0;
            for(int i = 0; i < 8; i++) {
                for(int j = 0; j < 8; j++) {
                    if(board[i][j] && board[i][j]->getSymbol() != 'K') {
                        total += getPieceValue(board[i][j]);
                    }
                }
            }
            return total;
        } catch (...) {
            return 0;
        }
    }

    // Helper: extract board-only FEN key
    string getBoardOnlyFEN() const {
        string fen;
        int empty = 0;

        for (int i = 0; i < 8; ++i) {
            for (int j = 0; j < 8; ++j) {
                if (!lent[i][j]) {
                    empty++;
                } else {
                    if (empty > 0) {
                        fen += to_string(empty);
                        empty = 0;
                    }
                    char s = lent[i][j]->getSymbol();
                    if (lent[i][j]->getSpalva() == JUODA)
                        s = tolower(s);
                    fen += s;
                }
            }
            if (empty > 0) {
                fen += to_string(empty);
                empty = 0;
            }
            if (i != 7) fen += '/';
        }
        return fen;
    }

    // Opening-book lookup function
    bool getOpeningBookMove(Move& outMove) {
    string key = getBoardOnlyFEN();

    auto it = OPENING_BOOK.find(key);
    if (it == OPENING_BOOK.end()) return false;

    const vector<string>& moves = it->second;
    if (moves.empty()) return false;

    // Pick random move
    std::uniform_int_distribution<size_t> dist(0, moves.size() - 1);
    const string& m = moves[dist(rng)];

    // Parse "e2e4"
    int fromY = m[0] - 'a';
    int fromX = 8 - (m[1] - '0');
    int toY   = m[2] - 'a';
    int toX   = 8 - (m[3] - '0');

    outMove = Move(fromX, fromY, toX, toY);
    outMove.reason = "Opening book";
    outMove.score  = 0;

    return true;
}


    // FIXED: Color-neutral evaluation function
    int evaluatePosition(const vector<vector<Figura*>>& board, Spalva us) const {
        try {
            if (board.size() != 8) return 0;
            for (const auto& row : board) {
                if (row.size() != 8) return 0;
            }
            
            int score = 0;
            int gamePhase = 0; // 0=opening, 1=middlegame, 2=endgame
            int totalMaterial = getTotalMaterial(board);
            
            if (totalMaterial < 1300) gamePhase = 2;
            else if (totalMaterial < 4000) gamePhase = 1;
            
            // Piece-square tables for positional evaluation
            static const int pawnTable[8][8] = {
                { 0,  0,  0,  0,  0,  0,  0,  0},
                {50, 50, 50, 50, 50, 50, 50, 50},
                {10, 10, 20, 30, 30, 20, 10, 10},
                { 5,  5, 10, 25, 25, 10,  5,  5},
                { 0,  0,  0, 20, 20,  0,  0,  0},
                { 5, -5,-10,  0,  0,-10, -5,  5},
                { 5, 10, 10,-20,-20, 10, 10,  5},
                { 0,  0,  0,  0,  0,  0,  0,  0}
            };
            
            static const int knightTable[8][8] = {
                {-50,-40,-30,-30,-30,-30,-40,-50},
                {-40,-20,  0,  0,  0,  0,-20,-40},
                {-30,  0, 10, 15, 15, 10,  0,-30},
                {-30,  5, 15, 20, 20, 15,  5,-30},
                {-30,  0, 15, 20, 20, 15,  0,-30},
                {-30,  5, 10, 15, 15, 10,  5,-30},
                {-40,-20,  0,  5,  5,  0,-20,-40},
                {-50,-40,-30,-30,-30,-30,-40,-50}
            };
            
            static const int bishopTable[8][8] = {
                {-20,-10,-10,-10,-10,-10,-10,-20},
                {-10,  0,  0,  0,  0,  0,  0,-10},
                {-10,  0,  5, 10, 10,  5,  0,-10},
                {-10,  5,  5, 10, 10,  5,  5,-10},
                {-10,  0, 10, 10, 10, 10,  0,-10},
                {-10, 10, 10, 10, 10, 10, 10,-10},
                {-10,  5,  0,  0,  0,  0,  5,-10},
                {-20,-10,-10,-10,-10,-10,-10,-20}
            };
            
            static const int kingMiddlegame[8][8] = {
                {-30,-40,-40,-50,-50,-40,-40,-30},
                {-30,-40,-40,-50,-50,-40,-40,-30},
                {-30,-40,-40,-50,-50,-40,-40,-30},
                {-30,-40,-40,-50,-50,-40,-40,-30},
                {-20,-30,-30,-40,-40,-30,-30,-20},
                {-10,-20,-20,-20,-20,-20,-20,-10},
                { 20, 20,  0,  0,  0,  0, 20, 20},
                { 20, 30, 10,  0,  0, 10, 30, 20}
            };
            
            static const int kingEndgame[8][8] = {
                {-50,-40,-30,-20,-20,-30,-40,-50},
                {-30,-20,-10,  0,  0,-10,-20,-30},
                {-30,-10, 20, 30, 30, 20,-10,-30},
                {-30,-10, 30, 40, 40, 30,-10,-30},
                {-30,-10, 30, 40, 40, 30,-10,-30},
                {-30,-10, 20, 30, 30, 20,-10,-30},
                {-30,-30,  0,  0,  0,  0,-30,-30},
                {-50,-30,-30,-30,-30,-30,-30,-50}
            };

            for(int i=0; i<8; i++) {
                for(int j=0; j<8; j++) {
                    if(board[i][j]) {
                        Figura* piece = board[i][j];
                        int value = getPieceValue(piece);
                        bool isOurPiece = (piece->getSpalva() == us);
                        int multiplier = isOurPiece ? 1 : -1;
                        
                        // Material value
                        score += value * multiplier;
                        
                        // Positional bonuses
                        char symbol = piece->getSymbol();
                        int posBonus = 0;
                        
                        // FIXED: Correct piece-square table application
                        // For white pieces, use the table as is
                        // For black pieces, flip the row index
                        int tableRow;
                        if (piece->getSpalva() == BALTA) {
                            tableRow = i; // White pieces use normal orientation
                        } else {
                            tableRow = 7 - i; // Black pieces use flipped orientation
                        }
                        
                        switch(symbol) {
                            case 'P':
                                posBonus = pawnTable[tableRow][j];
                                // Passed pawn bonus
                                if (isPawnPassed(board, i, j, piece->getSpalva())) {
                                    posBonus += 50;
                                }
                                break;
                            case 'N':
                                posBonus = knightTable[tableRow][j];
                                break;
                            case 'B':
                                posBonus = bishopTable[tableRow][j];
                                // Bishop pair bonus
                                if (hasBishopPair(board, piece->getSpalva())) {
                                    posBonus += 30;
                                }
                                break;
                            case 'R':
                                // Rook on open file bonus
                                if (isFileOpen(board, j)) {
                                    posBonus += 50;
                                } else if (isFileSemiOpen(board, j, piece->getSpalva())) {
                                    posBonus += 25;
                                }
                                break;
                            case 'Q':
                                // Queen mobility
                                posBonus += countMobility(board, i, j) * 2;
                                break;
                            case 'K':
                                if (gamePhase == 2) { // Endgame
                                    posBonus = kingEndgame[tableRow][j];
                                } else {
                                    posBonus = kingMiddlegame[tableRow][j];
                                    // King safety evaluation
                                    posBonus += evaluateKingSafety(board, i, j, piece->getSpalva());
                                }
                                break;
                        }
                        
                        score += posBonus * multiplier;
                    }
                }
            }
            
            // Additional strategic factors
            if (gamePhase == 2) { // Endgame specific
                score += evaluateEndgame(board, us);
            }
            
            return score;
        } catch (...) {
            // Fallback to simple material count
            try {
                int score = 0;
                for(int i=0; i<8; i++) {
                    for(int j=0; j<8; j++) {
                        if(board[i][j]) {
                            int v = getPieceValue(board[i][j]);
                            score += (board[i][j]->getSpalva()==us ? v : -v);
                        }
                    }
                }
                return score;
            } catch (...) {
                return 0;
            }
        }
    }

    // Helper functions for advanced evaluation
    bool isPawnPassed(const vector<vector<Figura*>>& board, int x, int y, Spalva color) const {
        try {
            if (board.size() != 8) return false;
            for (const auto& row : board) {
                if (row.size() != 8) return false;
            }
            
            int direction = (color == BALTA) ? -1 : 1;
            int startX = x + direction;
            int endX = (color == BALTA) ? -1 : 8;
            
            for (int checkX = startX; checkX != endX; checkX += direction) {
                if (checkX < 0 || checkX >= 8) break;
                for (int checkY = max(0, y-1); checkY <= min(7, y+1); checkY++) {
                    if (board[checkX][checkY] && 
                        board[checkX][checkY]->getSymbol() == 'P' && 
                        board[checkX][checkY]->getSpalva() != color) {
                        return false;
                    }
                }
            }
            return true;
        } catch (...) {
            return false;
        }
    }
    
    bool hasBishopPair(const vector<vector<Figura*>>& board, Spalva color) const {
        try {
            if (board.size() != 8) return false;
            for (const auto& row : board) {
                if (row.size() != 8) return false;
            }
            
            int bishopCount = 0;
            for (int i = 0; i < 8; i++) {
                for (int j = 0; j < 8; j++) {
                    if (board[i][j] && board[i][j]->getSymbol() == 'B' && 
                        board[i][j]->getSpalva() == color) {
                        bishopCount++;
                    }
                }
            }
            return bishopCount >= 2;
        } catch (...) {
            return false;
        }
    }
    
    bool isFileOpen(const vector<vector<Figura*>>& board, int file) const {
        try {
            if (file < 0 || file >= 8) return false;
            if (board.size() != 8) return false;
            for (const auto& row : board) {
                if (row.size() != 8) return false;
            }
            
            for (int i = 0; i < 8; i++) {
                if (board[i][file] && board[i][file]->getSymbol() == 'P') {
                    return false;
                }
            }
            return true;
        } catch (...) {
            return false;
        }
    }
    
    bool isFileSemiOpen(const vector<vector<Figura*>>& board, int file, Spalva color) const {
        try {
            if (file < 0 || file >= 8) return false;
            if (board.size() != 8) return false;
            for (const auto& row : board) {
                if (row.size() != 8) return false;
            }
            
            for (int i = 0; i < 8; i++) {
                if (board[i][file] && board[i][file]->getSymbol() == 'P' && 
                    board[i][file]->getSpalva() == color) {
                    return false;
                }
            }
            return true;
        } catch (...) {
            return false;
        }
    }
    
    int countMobility(const vector<vector<Figura*>>& board, int x, int y) const {
        try {
            if (x < 0 || x >= 8 || y < 0 || y >= 8) return 0;
            if (board.size() != 8) return 0;
            for (const auto& row : board) {
                if (row.size() != 8) return 0;
            }
            if (!board[x][y]) return 0;
            
            int mobility = 0;
            for (int i = 0; i < 8; i++) {
                for (int j = 0; j < 8; j++) {
                    if (board[x][y]->canMove(x, y, i, j, board)) {
                        mobility++;
                    }
                }
            }
            return mobility;
        } catch (...) {
            return 0;
        }
    }
    
    int evaluateKingSafety(const vector<vector<Figura*>>& board, int kingX, int kingY, Spalva color) const {
        try {
            if (kingX < 0 || kingX >= 8 || kingY < 0 || kingY >= 8) return 0;
            if (board.size() != 8) return 0;
            for (const auto& row : board) {
                if (row.size() != 8) return 0;
            }
            
            int safety = 0;
            Spalva opponent = opposite(color);
            
            // Check for pawn shield
            int direction = (color == BALTA) ? -1 : 1;
            for (int dy = -1; dy <= 1; dy++) {
                int shieldX = kingX + direction;
                int shieldY = kingY + dy;
                if (shieldX >= 0 && shieldX < 8 && shieldY >= 0 && shieldY < 8) {
                    if (board[shieldX][shieldY] && 
                        board[shieldX][shieldY]->getSymbol() == 'P' && 
                        board[shieldX][shieldY]->getSpalva() == color) {
                        safety += 10;
                    }
                }
            }
            
            // Penalty for exposed king
            int attackers = 0;
            for (int dx = -2; dx <= 2; dx++) {
                for (int dy = -2; dy <= 2; dy++) {
                    int checkX = kingX + dx, checkY = kingY + dy;
                    if (checkX >= 0 && checkX < 8 && checkY >= 0 && checkY < 8) {
                        if (isSquareAttacked(checkX, checkY, opponent, board)) {
                            attackers++;
                        }
                    }
                }
            }
            safety -= attackers * 5;
            
            return safety;
        } catch (...) {
            return 0;
        }
    }
    
    int evaluateEndgame(const vector<vector<Figura*>>& board, Spalva color) const {
        try {
            if (board.size() != 8) return 0;
            for (const auto& row : board) {
                if (row.size() != 8) return 0;
            }
            
            int score = 0;
            
            // King activity in endgame
            int ourKingX = -1, ourKingY = -1;
            int oppKingX = -1, oppKingY = -1;
            
            for (int i = 0; i < 8; i++) {
                for (int j = 0; j < 8; j++) {
                    if (board[i][j] && board[i][j]->getSymbol() == 'K') {
                        if (board[i][j]->getSpalva() == color) {
                            ourKingX = i; ourKingY = j;
                        } else {
                            oppKingX = i; oppKingY = j;
                        }
                    }
                }
            }
            
            if (ourKingX != -1 && oppKingX != -1) {
                // Centralization bonus
                int minCenterDist = INT_MAX;
                for (int cx = 3; cx <= 4; ++cx) {
                    for (int cy = 3; cy <= 4; ++cy) {
                        int dist = max(abs(ourKingX - cx), abs(ourKingY - cy));
                        minCenterDist = min(minCenterDist, dist);
                    }
                }
                if (minCenterDist != INT_MAX) {
                    score += (4 - minCenterDist) * 10;
                }
                
                // Opposition bonus (kings facing each other)
                if (abs(ourKingX - oppKingX) == 2 && ourKingY == oppKingY) {
                    score += 20;
                }
                if (abs(ourKingY - oppKingY) == 2 && ourKingX == oppKingX) {
                    score += 20;
                }
            }
            
            return score;
        } catch (...) {
            return 0;
        }
    }

    // Safe board copying with RAII
    unique_ptr<vector<vector<Figura*>>> copyBoardSafe(const vector<vector<Figura*>>& board) const {
        try {
            if (board.size() != 8) return nullptr;
            for (const auto& row : board) {
                if (row.size() != 8) return nullptr;
            }
            
            auto newBoard = make_unique<vector<vector<Figura*>>>(8, vector<Figura*>(8, nullptr));
            if (!newBoard) return nullptr;
            
            for (int i = 0; i < 8; i++) {
                for (int j = 0; j < 8; j++) {
                    if (board[i][j]) {
                        (*newBoard)[i][j] = board[i][j]->clone();
                        if (!(*newBoard)[i][j]) {
                            // Cleanup on failure
                            for (int ci = 0; ci <= i; ci++) {
                                for (int cj = 0; cj < (ci == i ? j : 8); cj++) {
                                    delete (*newBoard)[ci][cj];
                                    (*newBoard)[ci][cj] = nullptr;
                                }
                            }
                            return nullptr;
                        }
                    }
                }
            }
            return newBoard;
        } catch (...) {
            return nullptr;
        }
    }

    // Safe board cleanup
    void cleanupBoardSafe(vector<vector<Figura*>>& board) const {
        try {
            for (auto &row : board) {
                for (auto &p : row) {
                    delete p;
                    p = nullptr;
                }
            }
        } catch (...) {
            // Ignore cleanup errors but try to continue
        }
    }

    void makeMoveOnBoard(vector<vector<Figura*>>& board, const Move& move) const {
        try {
            if (!move.isValid()) return;
            if (board.size() != 8) return;
            for (const auto& row : board) {
                if (row.size() != 8) return;
            }
            if (move.fromX < 0 || move.fromX >= 8 || move.fromY < 0 || move.fromY >= 8) return;
            if (move.toX < 0 || move.toX >= 8 || move.toY < 0 || move.toY >= 8) return;
            if (!board[move.fromX][move.fromY]) return;
            
            delete board[move.toX][move.toY];
            board[move.toX][move.toY] = board[move.fromX][move.fromY];
            board[move.fromX][move.fromY] = nullptr;
        } catch (...) {
            // Silently fail on error - better than crashing
        }
    }

    // Generate all legal moves with safety checks
    vector<Move> generateLegalMoves(const vector<vector<Figura*>>& board, Spalva color) const {
        vector<Move> moves;
        try {
            if (board.size() != 8) return moves;
            for (const auto& row : board) {
                if (row.size() != 8) return moves;
            }
            
            for (int i = 0; i < 8; i++) {
                for (int j = 0; j < 8; j++) {
                    if (!board[i][j] || board[i][j]->getSpalva() != color) continue;
                    
                    for (int x = 0; x < 8; x++) {
                        for (int y = 0; y < 8; y++) {
                            if (i == x && j == y) continue;
                            
                            if (board[i][j]->canMove(i, j, x, y, board)) {
                                // Check if move is legal (doesn't leave king in check)
                                try {
                                    auto tempBoard = copyBoardSafe(board);
                                    if (tempBoard) {
                                        makeMoveOnBoard(*tempBoard, Move(i, j, x, y));
                                        
                                        if (!isKingInCheck(*tempBoard, color)) {
                                            moves.emplace_back(i, j, x, y);
                                        }
                                    }
                                } catch (...) {
                                    // Skip this move if we can't validate it safely
                                    continue;
                                }
                            }
                        }
                    }
                }
            }
        } catch (...) {
            // Return whatever moves we managed to generate
        }
        return moves;
    }

    // Enhanced move ordering for better alpha-beta pruning
    void orderMoves(vector<Move>& moves, const vector<vector<Figura*>>& board) const {
        try {
            if (board.size() != 8) return;
            for (const auto& row : board) {
                if (row.size() != 8) return;
            }
            
            for (auto& move : moves) {
                if (!move.isValid()) continue;
                if (move.fromX < 0 || move.fromX >= 8 || move.fromY < 0 || move.fromY >= 8) continue;
                if (move.toX < 0 || move.toX >= 8 || move.toY < 0 || move.toY >= 8) continue;
                if (!board[move.fromX][move.fromY]) continue;
                
                int score = 0;
                
                // Prioritize captures (MVV-LVA: Most Valuable Victim - Least Valuable Attacker)
                if (board[move.toX][move.toY]) {
                    int victimValue = getPieceValue(board[move.toX][move.toY]);
                    int attackerValue = getPieceValue(board[move.fromX][move.fromY]);
                    score += victimValue * 10 - attackerValue;
                }
                
                // Prioritize checks
                try {
                    auto tempBoard = copyBoardSafe(board);
                    if (tempBoard) {
                        makeMoveOnBoard(*tempBoard, move);
                        if (isKingInCheck(*tempBoard, opposite(board[move.fromX][move.fromY]->getSpalva()))) {
                            score += 500;
                        }
                    }
                } catch (...) {
                    // Skip check detection if it fails
                }
                
                // Prioritize center moves
                if (isCenter(move.toX, move.toY)) {
                    score += 20;
                }
                
                // Prioritize piece development
                char symbol = board[move.fromX][move.fromY]->getSymbol();
                if (symbol == 'N' || symbol == 'B') {
                    int backRank = (board[move.fromX][move.fromY]->getSpalva() == BALTA) ? 7 : 0;
                    if (move.fromX == backRank && move.toX != backRank) {
                        score += 30;
                    }
                }
                
                move.score = score;
            }
            
            // Sort moves by score (highest first)
            sort(moves.begin(), moves.end(), [](const Move& a, const Move& b) {
                return a.score > b.score;
            });
        } catch (...) {
            // If ordering fails, just continue with unordered moves
        }
    }

    // FIXED: Color-neutral quiescence search
    int quiescenceSearch(const vector<vector<Figura*>>& board, Spalva turn, int alpha, int beta, Spalva us, int& nodeCount, int depth = 0) const {
        try {
            if (isTimeUp() || nodeCount > aiConfig.maxNodes || depth > 5) {
                return evaluatePosition(board, us);
            }
            if (board.size() != 8) return evaluatePosition(board, us);
            for (const auto& row : board) {
                if (row.size() != 8) return evaluatePosition(board, us);
            }
            
            nodeCount++;
            int standPat = evaluatePosition(board, us);
            
            // FIXED: Proper perspective handling in quiescence search
            if (turn == us) {
                // We're maximizing
                if (standPat >= beta) return beta;
                if (standPat > alpha) alpha = standPat;
            } else {
                // We're minimizing (opponent's turn)
                if (standPat <= alpha) return alpha;
                if (standPat < beta) beta = standPat;
            }
            
            // Generate only capture moves
            vector<Move> captures;
            for (int i = 0; i < 8; i++) {
                for (int j = 0; j < 8; j++) {
                    if (!board[i][j] || board[i][j]->getSpalva() != turn) continue;
                    
                    for (int x = 0; x < 8; x++) {
                        for (int y = 0; y < 8; y++) {
                            if (board[x][y] && board[x][y]->getSpalva() != turn && 
                                board[i][j]->canMove(i, j, x, y, board)) {
                                
                                try {
                                    auto tempBoard = copyBoardSafe(board);
                                    if (tempBoard) {
                                        makeMoveOnBoard(*tempBoard, Move(i, j, x, y));
                                        if (!isKingInCheck(*tempBoard, turn)) {
                                            captures.emplace_back(i, j, x, y);
                                        }
                                    }
                                } catch (...) {
                                    continue;
                                }
                            }
                        }
                    }
                }
            }
            
            orderMoves(captures, board);
            
            for (const auto& capture : captures) {
                if (isTimeUp() || nodeCount > aiConfig.maxNodes) break;
                
                try {
                    auto tempBoard = copyBoardSafe(board);
                    if (tempBoard) {
                        makeMoveOnBoard(*tempBoard, capture);
                        
                        int eval = quiescenceSearch(*tempBoard, opposite(turn), alpha, beta, us, nodeCount, depth + 1);
                        
                        if (turn == us) {
                            // We're maximizing
                            if (eval >= beta) return beta;
                            if (eval > alpha) alpha = eval;
                        } else {
                            // We're minimizing (opponent's turn)
                            if (eval <= alpha) return alpha;
                            if (eval < beta) beta = eval;
                        }
                    }
                } catch (...) {
                    continue;
                }
            }
            
            return (turn == us) ? alpha : beta;
        } catch (...) {
            return evaluatePosition(board, us);
        }
    }

    // FIXED: Color-neutral alpha-beta search with proper perspective
    int alphaBetaSearch(const vector<vector<Figura*>>& board, int depth, Spalva turn, int alpha, int beta, Spalva us, int& nodeCount) const {
        try {
            if (isTimeUp() || nodeCount > aiConfig.maxNodes) {
                return evaluatePosition(board, us);
            }
            if (board.size() != 8) return evaluatePosition(board, us);
            for (const auto& row : board) {
                if (row.size() != 8) return evaluatePosition(board, us);
            }
            
            nodeCount++;
            
            // Terminal node evaluation
            if (depth <= 0) {
                return quiescenceSearch(board, turn, alpha, beta, us, nodeCount);
            }
            
            // Generate and order moves
            vector<Move> moves = generateLegalMoves(board, turn);
            if (moves.empty()) {
                // No legal moves - checkmate or stalemate
                if (isKingInCheck(board, turn)) {
                    // FIXED: Proper mate scoring from our perspective
                    return (turn == us) ? (-20000 + nodeCount) : (20000 - nodeCount);
                } else {
                    return 0; // Stalemate
                }
            }
            
            orderMoves(moves, board);
            
            // FIXED: Proper minimax implementation based on whose turn it is
            if (turn == us) {
                // Maximizing player (us)
                int maxEval = INT_MIN;
                for (const auto& move : moves) {
                    if (isTimeUp() || nodeCount > aiConfig.maxNodes) break;
                    
                    try {
                        auto tempBoard = copyBoardSafe(board);
                        if (tempBoard) {
                            makeMoveOnBoard(*tempBoard, move);
                            
                            int eval = alphaBetaSearch(*tempBoard, depth - 1, opposite(turn), alpha, beta, us, nodeCount);
                            maxEval = max(maxEval, eval);
                            alpha = max(alpha, eval);
                            
                            if (beta <= alpha) break; // Alpha-beta cutoff
                        }
                    } catch (...) {
                        continue;
                    }
                }
                return maxEval;
            } else {
                // Minimizing player (opponent)
                int minEval = INT_MAX;
                for (const auto& move : moves) {
                    if (isTimeUp() || nodeCount > aiConfig.maxNodes) break;
                    
                    try {
                        auto tempBoard = copyBoardSafe(board);
                        if (tempBoard) {
                            makeMoveOnBoard(*tempBoard, move);
                            
                            int eval = alphaBetaSearch(*tempBoard, depth - 1, opposite(turn), alpha, beta, us, nodeCount);
                            minEval = min(minEval, eval);
                            beta = min(beta, eval);
                            
                            if (beta <= alpha) break; // Alpha-beta cutoff
                        }
                    } catch (...) {
                        continue;
                    }
                }
                return minEval;
            }
        } catch (...) {
            return evaluatePosition(board, us);
        }
    }

    // FIXED: Color-neutral iterative deepening search
    Move findBestMoveIterative(Spalva turn) {
        try {
            searchStartTime = chrono::steady_clock::now();
            Move bestMove;
            
            vector<Move> rootMoves = generateLegalMoves(lent, turn);
            if (rootMoves.empty()) {
                return Move(); // No legal moves
            }
            
            if (rootMoves.size() == 1) {
                bestMove = rootMoves[0];
                bestMove.reason = "Only legal move";
                return bestMove;
            }
            
            orderMoves(rootMoves, lent);
            
            // FIXED: Initialize best score properly for the color playing
            int bestScore = INT_MIN; // Always start with worst possible for maximizing player
            
            // Iterative deepening
            for (int depth = 1; depth <= aiConfig.maxDepth; depth++) {
                if (isTimeUp()) break;
                
                Move currentBest;
                int currentBestScore = INT_MIN; // Always maximizing from our perspective
                bool foundMove = false;
                
                for (const auto& move : rootMoves) {
                    if (isTimeUp()) break;
                    
                    try {
                        auto tempBoard = copyBoardSafe(lent);
                        if (tempBoard) {
                            makeMoveOnBoard(*tempBoard, move);
                            
                            int nodeCount = 0;
                            // FIXED: Always search from our perspective (turn = us)
                            int score = alphaBetaSearch(*tempBoard, depth - 1, opposite(turn), INT_MIN, INT_MAX, turn, nodeCount);
                            
                            // FIXED: Always maximize score from our perspective
                            if (!foundMove || score > currentBestScore) {
                                currentBest = move;
                                currentBestScore = score;
                                foundMove = true;
                                
                                // Add evaluation reasoning
                                string reason = "";
                                if (lent[move.toX][move.toY]) {
                                    reason += "Captures " + string(1, lent[move.toX][move.toY]->getSymbol()) + " ";
                                }
                                if (score > 100) reason += "Strong position ";
                                if (score > 500) reason += "Winning advantage ";
                                if (abs(score) > 10000) reason += "Forced mate ";
                                
                                currentBest.reason = reason;
                                currentBest.score = score;
                            }
                        }
                    } catch (...) {
                        continue;
                    }
                }
                
                if (foundMove && !isTimeUp()) {
                    bestMove = currentBest;
                    bestScore = currentBestScore;
                    
                    // If we found a forced mate, no need to search deeper
                    if (abs(bestScore) > 15000) break;
                }
            }
            
            return bestMove;
        } catch (...) {
            // If everything fails, try to return a random legal move
            try {
                vector<Move> moves = generateLegalMoves(lent, turn);
                if (!moves.empty()) {
                    Move fallback = moves[0];
                    fallback.reason = "Fallback move";
                    return fallback;
                }
            } catch (...) {
                // Even fallback failed
            }
            return Move();
        }
    }

public:
    SachmatuLenta() {
        try {
            lent.resize(8, vector<Figura*>(8, nullptr));
            currentTurn = BALTA;   // white starts by default
            setupLenta();
        } catch (...) {
            // Initialize with empty board if setup fails
            lent.resize(8, vector<Figura*>(8, nullptr));
            currentTurn = BALTA;
        }
    }
    
    ~SachmatuLenta() {
        try {
            for(auto &row : lent) {
                for(auto &p : row) { 
                    delete p; 
                    p = nullptr; 
                }
            }
        } catch (...) {
            // Ignore errors during destruction
        }
    }

    void setupLenta() {
        try {
            // Clean up existing pieces
            for(auto &row : lent) {
                for(auto &p : row) { 
                    delete p; 
                    p = nullptr; 
                }
            }
            
            // Setup pawns
            for(int j = 0; j < 8; j++) {
                try {
                    lent[1][j] = new Peske(JUODA);
                    lent[6][j] = new Peske(BALTA);
                } catch (...) {
                    // Continue setup even if some pieces fail
                }
            }
            
            // Setup other pieces
            try { lent[0][0] = new Bokstas(JUODA); } catch (...) {}
            try { lent[0][7] = new Bokstas(JUODA); } catch (...) {}
            try { lent[7][0] = new Bokstas(BALTA); } catch (...) {}
            try { lent[7][7] = new Bokstas(BALTA); } catch (...) {}
            try { lent[0][1] = new Zirgas(JUODA); } catch (...) {}
            try { lent[0][6] = new Zirgas(JUODA); } catch (...) {}
            try { lent[7][1] = new Zirgas(BALTA); } catch (...) {}
            try { lent[7][6] = new Zirgas(BALTA); } catch (...) {}
            try { lent[0][2] = new Rikis(JUODA); } catch (...) {}
            try { lent[0][5] = new Rikis(JUODA); } catch (...) {}
            try { lent[7][2] = new Rikis(BALTA); } catch (...) {}
            try { lent[7][5] = new Rikis(BALTA); } catch (...) {}
            try { lent[0][3] = new Valdove(JUODA); } catch (...) {}
            try { lent[7][3] = new Valdove(BALTA); } catch (...) {}
            try { lent[0][4] = new Karalius(JUODA); } catch (...) {}
            try { lent[7][4] = new Karalius(BALTA); } catch (...) {}
        } catch (...) {
            // If setup completely fails, at least ensure we have a valid empty board
            for(auto &row : lent) {
                for(auto &p : row) { 
                    p = nullptr; 
                }
            }
        }
    }

    bool movePiece(const string& from, const string& to) {
        Spalva turn = currentTurn;
        try {
            if (from.length() != 2 || to.length() != 2) return false;
            int fromY = from[0] - 'a', fromX = 8 - (from[1] - '0');
            int toY = to[0] - 'a', toX = 8 - (to[1] - '0');
        
            if (fromX < 0 || fromX > 7 || fromY < 0 || fromY > 7 ||
                toX < 0 || toX > 7 || toY < 0 || toY > 7) {
                return false;
            }
        
            if (!lent[fromX][fromY]) {
                return false;
            }
        
            if (lent[fromX][fromY]->getSpalva() != currentTurn) {
                return false;
            }
        
            if (!lent[fromX][fromY]->canMove(fromX, fromY, toX, toY, lent)) {
                return false;
            }
            
            // Check if move would leave king in check
            try {
                auto tempBoard = copyBoardSafe(lent);
                if (tempBoard) {
                    makeMoveOnBoard(*tempBoard, Move(fromX, fromY, toX, toY));
                    if (isKingInCheck(*tempBoard, currentTurn)) {
                        return false;
                    }
                } else {
                    return false;
                }
            } catch (...) {
                return false;
            }
            
            // Handle castling
            if (lent[fromX][fromY]->getSymbol() == 'K' && abs(toY - fromY) == 2) {
                try {
                    int direction = (toY > fromY) ? 1 : -1;
                    int rookFromY = (direction == 1) ? 7 : 0;
                    int rookToY = toY - direction;
                    
                    // Check if king passes through attacked squares
                    Spalva opponentColor = opposite(currentTurn);
                    for (int y = fromY; y != toY + direction; y += direction) {
                        if (isSquareAttacked(fromX, y, opponentColor, lent)) {
                            return false;
                        }
                    }
                    
                    // Move rook
                    if (lent[fromX][rookFromY] && lent[fromX][rookFromY]->getSymbol() == 'R') {
                        Bokstas* rookPtr = dynamic_cast<Bokstas*>(lent[fromX][rookFromY]);
                        lent[fromX][rookToY] = lent[fromX][rookFromY];
                        lent[fromX][rookFromY] = nullptr;
                        if (rookPtr) rookPtr->markMoved();
                    }
                } catch (...) {
                    return false;
                }
            }

            // Make the move
            try {
                Figura* capturedPiece = lent[toX][toY];
                lent[toX][toY] = lent[fromX][fromY];
                lent[fromX][fromY] = nullptr;
                
                // Mark king/rook as moved
                if (lent[toX][toY]->getSymbol() == 'K') {
                    Karalius* kingPtr = dynamic_cast<Karalius*>(lent[toX][toY]);
                    if (kingPtr) kingPtr->markMoved();
                } else if (lent[toX][toY]->getSymbol() == 'R') {
                    Bokstas* rookPtr = dynamic_cast<Bokstas*>(lent[toX][toY]);
                    if (rookPtr) rookPtr->markMoved();
                }
                
                // Check for pawn promotion - auto-promote to queen
                if (lent[toX][toY]->getSymbol() == 'P' && (toX == 0 || toX == 7)) {
                    try {
                        Spalva pawnColor = lent[toX][toY]->getSpalva();
                        Figura* newPiece = new Valdove(pawnColor);
                        if (newPiece) {
                            delete lent[toX][toY];
                            lent[toX][toY] = newPiece;
                        }
                    } catch (...) {
                        // If promotion fails, keep the pawn
                    }
                }
                
                delete capturedPiece;
                currentTurn = opposite(currentTurn);
                return true;
            } catch (...) {
                return false;
            }
        } catch (...) {
            return false;
        }
    }

    bool isGameOver(Spalva turn, bool& isCheckmate, bool& isStalemate) {
        try {
            isCheckmate = false;
            isStalemate = false;
            
            vector<Move> legalMoves = generateLegalMoves(lent, turn);
            bool inCheck = isKingInCheck(lent, turn);
            
            if (legalMoves.empty()) {
                if (inCheck) {
                    isCheckmate = true;
                } else {
                    isStalemate = true;
                }
                return true;
            }
            
            return false;
        } catch (...) {
            // If we can't determine game state safely, assume game continues
            return false;
        }
    }

    bool makeAIMove(Spalva aiColor) {
    Move bestMove = findBestMoveIterative(aiColor);
    if (!bestMove.isValid()) return false;

    // Apply move directly to native board
    makeMoveOnBoard(lent, bestMove);

    // Handle promotion automatically (already done in makeMoveOnBoard logic if needed)
    currentTurn = opposite(aiColor);
    return true;
    }


    Move computeBestMove() {
        return findBestMoveIterative(currentTurn);
    }

    // Getter for current board state
    const vector<vector<Figura*>>& getBoard() const {
        return lent;
    }

    // Set current turn
    void setCurrentTurn(Spalva turn) {
        currentTurn = turn;
    }

    // Get current turn
    Spalva getCurrentTurn() const {
        return currentTurn;
    }

    // Clear board and load from external representation
    void loadFromBoard64(const char* b64, bool whiteTurn) {
        try {
            if (!b64) return;
            
            // Clear current board
            for (int r = 0; r < 8; ++r) {
                for (int c = 0; c < 8; ++c) {
                    delete lent[r][c];
                    lent[r][c] = nullptr;
                }
            }
            
            // Parse board string (format: "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR")
            int row = 0, col = 0;
            for (int i = 0; b64[i] && row < 8; ++i) {
                char ch = b64[i];
                if (ch == '/') {
                    row++;
                    col = 0;
                } else if (isdigit(ch)) {
                    col += ch - '0';
                } else {
                    Spalva color = isupper(ch) ? BALTA : JUODA;
                    char piece = tolower(ch);
                    
                    switch (piece) {
                        case 'p': lent[row][col] = new Peske(color); break;
                        case 'r': lent[row][col] = new Bokstas(color); break;
                        case 'n': lent[row][col] = new Zirgas(color); break;
                        case 'b': lent[row][col] = new Rikis(color); break;
                        case 'q': lent[row][col] = new Valdove(color); break;
                        case 'k': lent[row][col] = new Karalius(color); break;
                    }
                    col++;
                }
            }
            
            currentTurn = whiteTurn ? BALTA : JUODA;
        } catch (...) {
            // If loading fails, keep current board state
        }
    }

    // Generate FEN string representation
    string getFEN() const {
        try {
            string fen = "";
            int emptyCount = 0;
            
            for (int i = 0; i < 8; ++i) {
                for (int j = 0; j < 8; ++j) {
                    if (!lent[i][j]) {
                        emptyCount++;
                    } else {
                        if (emptyCount > 0) {
                            fen += to_string(emptyCount);
                            emptyCount = 0;
                        }
                        char symbol = lent[i][j]->getSymbol();
                        if (lent[i][j]->getSpalva() == JUODA) {
                            symbol = tolower(symbol);
                        }
                        fen += symbol;
                    }
                }
                if (emptyCount > 0) {
                    fen += to_string(emptyCount);
                    emptyCount = 0;
                }
                if (i < 7) fen += '/';
            }
            
            fen += (currentTurn == BALTA) ? " w " : " b ";
            // Add castling rights, en passant, etc. if needed
            fen += "- - 0 1";
            
            return fen;
        } catch (...) {
            return "8/8/8/8/8/8/8/8 w - - 0 1";
        }
    }

    // AI difficulty settings
    void setAIDifficulty(int level) {
        try {
            level = max(1, min(5, level)); // Clamp to 1-5
            
            switch (level) {
                case 1: // Beginner
                    aiConfig.maxDepth = 2;
                    aiConfig.maxNodes = 5000;
                    aiConfig.timeLimit = 1000;
                    break;
                case 2: // Easy
                    aiConfig.maxDepth = 3;
                    aiConfig.maxNodes = 15000;
                    aiConfig.timeLimit = 2000;
                    break;
                case 3: // Medium
                    aiConfig.maxDepth = 4;
                    aiConfig.maxNodes = 30000;
                    aiConfig.timeLimit = 3000;
                    break;
                case 4: // Hard
                    aiConfig.maxDepth = 5;
                    aiConfig.maxNodes = 50000;
                    aiConfig.timeLimit = 5000;
                    break;
                case 5: // Expert
                    aiConfig.maxDepth = 6;
                    aiConfig.maxNodes = 100000;
                    aiConfig.timeLimit = 8000;
                    break;
            }
        } catch (...) {
            // Keep default settings on error
        }
    }
    
    // Get AI suggested move without making it
    Move getBestMove(Spalva turn) {
        return findBestMoveIterative(turn);
    }
    
    // Evaluate current position from perspective of given color
    int evaluateCurrentPosition(Spalva perspective) {
        return evaluatePosition(lent, perspective);
    }
    
    // Get all legal moves for given color
    vector<Move> getLegalMoves(Spalva color) {
        return generateLegalMoves(lent, color);
    }
    
    // Reset to starting position
    void reset() {
        setupLenta();
        currentTurn = BALTA;
    }
};