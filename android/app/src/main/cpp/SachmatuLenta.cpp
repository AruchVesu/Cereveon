#include "SachmatuLenta.h"
#include <cstring>
#include <cctype>
#include <algorithm>
#include <chrono>
#include <random>
#include <climits>

// ── Static storage ─────────────────────────────────────────────────────────
SachmatuLenta::TTEntry SachmatuLenta::ttTable[SachmatuLenta::TT_SIZE];
uint64_t SachmatuLenta::ZPC[2][7][8][8];
uint64_t SachmatuLenta::ZSIDE;
uint64_t SachmatuLenta::ZCAST[16];
uint64_t SachmatuLenta::ZEP[8];
bool     SachmatuLenta::s_zobristReady = false;

// ── Zobrist initialisation ─────────────────────────────────────────────────
void SachmatuLenta::initZobrist() {
    if (s_zobristReady) return;
    std::mt19937_64 rng(0xDEADBEEFCAFEBABEULL);
    for (int c = 0; c < 2; c++)
        for (int p = 0; p < 7; p++)
            for (int r = 0; r < 8; r++)
                for (int f = 0; f < 8; f++)
                    ZPC[c][p][r][f] = rng();
    ZSIDE = rng();
    for (int i = 0; i < 16; i++) ZCAST[i] = rng();
    for (int i = 0;  i < 8;  i++) ZEP[i]   = rng();
    s_zobristReady = true;
}

uint64_t SachmatuLenta::computeHash() const {
    uint64_t h = 0;
    for (int r = 0; r < 8; r++)
        for (int c = 0; c < 8; c++)
            if (board[r][c].type != NONE)
                h ^= ZPC[board[r][c].color][board[r][c].type][r][c];
    if (currentTurn == JUODA) h ^= ZSIDE;
    h ^= ZCAST[castling];
    if (epFile >= 0) h ^= ZEP[epFile];
    return h;
}

// ── Time control ───────────────────────────────────────────────────────────
bool SachmatuLenta::timeUp() const {
    using ms = std::chrono::milliseconds;
    auto elapsed = std::chrono::duration_cast<ms>(
        std::chrono::steady_clock::now() - searchStart).count();
    return elapsed >= timeLimitMs;
}

// ── Life-cycle ─────────────────────────────────────────────────────────────
SachmatuLenta::SachmatuLenta() {
    initZobrist();
    reset();
}

void SachmatuLenta::reset() { setupLenta(); }

void SachmatuLenta::setupLenta() {
    loadFromBoard64("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1");
}

// ── FEN loader ─────────────────────────────────────────────────────────────
static PType charToPType(char c) {
    switch (c | 32) {  // tolower
        case 'p': return PAWN;
        case 'n': return KNIGHT;
        case 'b': return BISHOP;
        case 'r': return ROOK;
        case 'q': return QUEEN;
        case 'k': return KING;
        default:  return NONE;
    }
}

void SachmatuLenta::loadFromBoard64(const char* fen) {
    memset(board, 0, sizeof(board));
    castling = 0; epFile = -1; epRank = -1; halfMoveClock = 0;
    currentTurn = BALTA;
    hashHistory.clear();

    if (!fen) { currentHash = computeHash(); return; }

    const char* p = fen;

    if (!strchr(fen, '/')) {
        // Flat 64-char board string
        for (int i = 0; i < 64 && *p && *p != ' '; i++, p++) {
            char c = *p;
            if (c == '.') continue;
            PType pt = charToPType(c);
            if (pt != NONE) {
                board[i/8][i%8].type  = pt;
                board[i/8][i%8].color = (c >= 'A' && c <= 'Z') ? BALTA : JUODA;
            }
        }
        currentHash = computeHash();
        return;
    }

    // FEN piece placement
    int r = 0, c = 0;
    while (*p && *p != ' ') {
        char ch = *p++;
        if (ch == '/') { r++; c = 0; }
        else if (ch >= '1' && ch <= '8') { c += ch - '0'; }
        else {
            PType pt = charToPType(ch);
            if (pt != NONE && r < 8 && c < 8) {
                board[r][c].type  = pt;
                board[r][c].color = (ch >= 'A' && ch <= 'Z') ? BALTA : JUODA;
            }
            c++;
        }
    }
    if (*p == ' ') p++;

    // Side to move
    if (*p == 'b') currentTurn = JUODA;
    while (*p && *p != ' ') p++;
    if (*p == ' ') p++;

    // Castling rights
    if (*p != '-') {
        while (*p && *p != ' ') {
            switch (*p++) {
                case 'K': castling |= 1; break;
                case 'Q': castling |= 2; break;
                case 'k': castling |= 4; break;
                case 'q': castling |= 8; break;
            }
        }
    } else { p++; }
    while (*p && *p != ' ') p++;
    if (*p == ' ') p++;

    // En passant
    if (*p >= 'a' && *p <= 'h') {
        epFile = *p++ - 'a';
        if (*p >= '1' && *p <= '8') {
            epRank = 8 - (*p++ - '0');
        }
    } else { while (*p && *p != ' ') p++; }
    while (*p && *p != ' ') p++;
    if (*p == ' ') p++;

    // Half-move clock
    while (*p >= '0' && *p <= '9')
        halfMoveClock = halfMoveClock * 10 + (*p++ - '0');

    currentHash = computeHash();
}

// ── toBoard64String ────────────────────────────────────────────────────────
static const char kPieceChars[] = ".pnbrqk";

std::string SachmatuLenta::toBoard64String() const {
    std::string s(64, '.');
    for (int r = 0; r < 8; r++) {
        for (int c = 0; c < 8; c++) {
            PType pt = board[r][c].type;
            if (pt == NONE) continue;
            char ch = kPieceChars[pt];
            if (board[r][c].color == BALTA) ch -= 32; // toupper
            s[r*8 + c] = ch;
        }
    }
    return s;
}

// ── promotePawn ────────────────────────────────────────────────────────────
bool SachmatuLenta::promotePawn(int r, int c, char type) {
    if (r < 0 || r > 7 || c < 0 || c > 7) return false;
    if (board[r][c].type != PAWN) return false;
    PType pt = charToPType(type);
    if (pt == NONE || pt == PAWN || pt == KING) return false;
    currentHash ^= ZPC[board[r][c].color][board[r][c].type][r][c];
    board[r][c].type = pt;
    currentHash ^= ZPC[board[r][c].color][board[r][c].type][r][c];
    return true;
}

// ── isPathClear ────────────────────────────────────────────────────────────
bool SachmatuLenta::isPathClear(int fr, int fc, int tr, int tc) const {
    int dr = (tr > fr) ? 1 : (tr < fr ? -1 : 0);
    int dc = (tc > fc) ? 1 : (tc < fc ? -1 : 0);
    int row = fr + dr, col = fc + dc;
    while (row != tr || col != tc) {
        if (board[row][col].type != NONE) return false;
        row += dr; col += dc;
    }
    return true;
}

// ── isSquareAttacked ───────────────────────────────────────────────────────
bool SachmatuLenta::isSquareAttacked(int r, int c, Spalva by) const {
    // Pawns: white pawns at (r+1,c±1) attack (r,c); black at (r-1,c±1)
    {
        int pr = (by == BALTA) ? r + 1 : r - 1;
        if (pr >= 0 && pr < 8) {
            if (c > 0 && board[pr][c-1].type == PAWN && board[pr][c-1].color == by) return true;
            if (c < 7 && board[pr][c+1].type == PAWN && board[pr][c+1].color == by) return true;
        }
    }
    // Knights
    static const int KN[8][2] = {{-2,-1},{-2,1},{-1,-2},{-1,2},{1,-2},{1,2},{2,-1},{2,1}};
    for (auto& d : KN) {
        int nr = r+d[0], nc = c+d[1];
        if (nr>=0&&nr<8&&nc>=0&&nc<8 && board[nr][nc].type==KNIGHT && board[nr][nc].color==by) return true;
    }
    // King
    for (int dr = -1; dr <= 1; dr++) for (int dc = -1; dc <= 1; dc++) {
        if (!dr && !dc) continue;
        int nr = r+dr, nc = c+dc;
        if (nr>=0&&nr<8&&nc>=0&&nc<8 && board[nr][nc].type==KING && board[nr][nc].color==by) return true;
    }
    // Rook / Queen (straight)
    static const int RD[4][2] = {{0,1},{0,-1},{1,0},{-1,0}};
    for (auto& d : RD) {
        for (int nr=r+d[0],nc=c+d[1]; nr>=0&&nr<8&&nc>=0&&nc<8; nr+=d[0],nc+=d[1]) {
            PType pt = board[nr][nc].type;
            if (pt != NONE) {
                if (board[nr][nc].color==by && (pt==ROOK||pt==QUEEN)) return true;
                break;
            }
        }
    }
    // Bishop / Queen (diagonal)
    static const int BD[4][2] = {{1,1},{1,-1},{-1,1},{-1,-1}};
    for (auto& d : BD) {
        for (int nr=r+d[0],nc=c+d[1]; nr>=0&&nr<8&&nc>=0&&nc<8; nr+=d[0],nc+=d[1]) {
            PType pt = board[nr][nc].type;
            if (pt != NONE) {
                if (board[nr][nc].color==by && (pt==BISHOP||pt==QUEEN)) return true;
                break;
            }
        }
    }
    return false;
}

// ── isInCheck ──────────────────────────────────────────────────────────────
bool SachmatuLenta::isInCheck(Spalva s) const {
    for (int r = 0; r < 8; r++)
        for (int c = 0; c < 8; c++)
            if (board[r][c].type==KING && board[r][c].color==s)
                return isSquareAttacked(r, c, opp(s));
    return false;
}

// ── isLegalMove (public API) ───────────────────────────────────────────────
bool SachmatuLenta::isLegalMove(int fr, int fc, int tr, int tc, Spalva s) const {
    SachmatuLenta* self = const_cast<SachmatuLenta*>(this);
    std::vector<Move> legal = self->generateLegalMoves(s);
    for (const auto& m : legal)
        if (m.fromX==fr && m.fromY==fc && m.toX==tr && m.toY==tc) return true;
    return false;
}

// ── generatePseudoLegal ────────────────────────────────────────────────────
void SachmatuLenta::generatePseudoLegal(Spalva s, std::vector<Move>& out) const {
    const int dir      = (s == BALTA) ? -1 : 1;
    const int startRow = (s == BALTA) ? 6  : 1;
    const int promRow  = (s == BALTA) ? 0  : 7;

    for (int fr = 0; fr < 8; fr++) {
        for (int fc = 0; fc < 8; fc++) {
            if (board[fr][fc].type == NONE || board[fr][fc].color != s) continue;
            const PType pt = board[fr][fc].type;

            switch (pt) {
            case PAWN: {
                int r1 = fr + dir;
                if (r1 >= 0 && r1 < 8 && board[r1][fc].type == NONE) {
                    if (r1 == promRow) {
                        for (uint8_t pp : {(uint8_t)'Q',(uint8_t)'R',(uint8_t)'B',(uint8_t)'N'})
                            out.emplace_back(fr,fc,r1,fc,pp);
                    } else {
                        out.emplace_back(fr,fc,r1,fc);
                        if (fr == startRow && board[fr+2*dir][fc].type == NONE)
                            out.emplace_back(fr,fc,fr+2*dir,fc);
                    }
                }
                for (int dc : {-1, 1}) {
                    int tc = fc + dc, tr = fr + dir;
                    if (tc<0||tc>7||tr<0||tr>7) continue;
                    bool cap = (board[tr][tc].type!=NONE && board[tr][tc].color!=s);
                    bool ep  = (tr==epRank && tc==epFile);
                    if (cap || ep) {
                        if (tr == promRow) {
                            for (uint8_t pp : {(uint8_t)'Q',(uint8_t)'R',(uint8_t)'B',(uint8_t)'N'})
                                out.emplace_back(fr,fc,tr,tc,pp);
                        } else {
                            out.emplace_back(fr,fc,tr,tc);
                        }
                    }
                }
                break;
            }
            case KNIGHT: {
                static const int KND[8][2] = {{-2,-1},{-2,1},{-1,-2},{-1,2},{1,-2},{1,2},{2,-1},{2,1}};
                for (auto& d : KND) {
                    int tr=fr+d[0], tc=fc+d[1];
                    if (tr<0||tr>7||tc<0||tc>7) continue;
                    if (board[tr][tc].type!=NONE && board[tr][tc].color==s) continue;
                    out.emplace_back(fr,fc,tr,tc);
                }
                break;
            }
            case BISHOP: {
                static const int BD[4][2] = {{1,1},{1,-1},{-1,1},{-1,-1}};
                for (auto& d : BD)
                    for (int tr=fr+d[0],tc=fc+d[1]; tr>=0&&tr<8&&tc>=0&&tc<8; tr+=d[0],tc+=d[1]) {
                        if (board[tr][tc].type!=NONE) { if(board[tr][tc].color!=s) out.emplace_back(fr,fc,tr,tc); break; }
                        out.emplace_back(fr,fc,tr,tc);
                    }
                break;
            }
            case ROOK: {
                static const int RD[4][2] = {{0,1},{0,-1},{1,0},{-1,0}};
                for (auto& d : RD)
                    for (int tr=fr+d[0],tc=fc+d[1]; tr>=0&&tr<8&&tc>=0&&tc<8; tr+=d[0],tc+=d[1]) {
                        if (board[tr][tc].type!=NONE) { if(board[tr][tc].color!=s) out.emplace_back(fr,fc,tr,tc); break; }
                        out.emplace_back(fr,fc,tr,tc);
                    }
                break;
            }
            case QUEEN: {
                static const int QD[8][2] = {{0,1},{0,-1},{1,0},{-1,0},{1,1},{1,-1},{-1,1},{-1,-1}};
                for (auto& d : QD)
                    for (int tr=fr+d[0],tc=fc+d[1]; tr>=0&&tr<8&&tc>=0&&tc<8; tr+=d[0],tc+=d[1]) {
                        if (board[tr][tc].type!=NONE) { if(board[tr][tc].color!=s) out.emplace_back(fr,fc,tr,tc); break; }
                        out.emplace_back(fr,fc,tr,tc);
                    }
                break;
            }
            case KING: {
                static const int KD[8][2] = {{0,1},{0,-1},{1,0},{-1,0},{1,1},{1,-1},{-1,1},{-1,-1}};
                for (auto& d : KD) {
                    int tr=fr+d[0], tc=fc+d[1];
                    if (tr<0||tr>7||tc<0||tc>7) continue;
                    if (board[tr][tc].type!=NONE && board[tr][tc].color==s) continue;
                    out.emplace_back(fr,fc,tr,tc);
                }
                // Castling: king must be on its home square and not in check
                Spalva opp2 = opp(s);
                if (s==BALTA && fr==7 && fc==4) {
                    if ((castling&1) && !board[7][5].type && !board[7][6].type &&
                        !isSquareAttacked(7,4,opp2) && !isSquareAttacked(7,5,opp2) && !isSquareAttacked(7,6,opp2))
                        out.emplace_back(7,4,7,6);
                    if ((castling&2) && !board[7][3].type && !board[7][2].type && !board[7][1].type &&
                        !isSquareAttacked(7,4,opp2) && !isSquareAttacked(7,3,opp2) && !isSquareAttacked(7,2,opp2))
                        out.emplace_back(7,4,7,2);
                } else if (s==JUODA && fr==0 && fc==4) {
                    if ((castling&4) && !board[0][5].type && !board[0][6].type &&
                        !isSquareAttacked(0,4,opp2) && !isSquareAttacked(0,5,opp2) && !isSquareAttacked(0,6,opp2))
                        out.emplace_back(0,4,0,6);
                    if ((castling&8) && !board[0][3].type && !board[0][2].type && !board[0][1].type &&
                        !isSquareAttacked(0,4,opp2) && !isSquareAttacked(0,3,opp2) && !isSquareAttacked(0,2,opp2))
                        out.emplace_back(0,4,0,2);
                }
                break;
            }
            default: break;
            }
        }
    }
}

// ── makeMove ───────────────────────────────────────────────────────────────
SachmatuLenta::UndoInfo SachmatuLenta::makeMove(const Move& m) {
    UndoInfo u{};
    u.castlingBefore      = castling;
    u.epFileBefore        = epFile;
    u.epRankBefore        = epRank;
    u.halfMoveClockBefore = halfMoveClock;
    u.hashBefore          = currentHash;
    u.rookFromCol         = -1;

    PieceCell moving   = board[m.fromX][m.fromY];
    u.capturedAtTo     = board[m.toX][m.toY];

    // Remove moving piece and any capture from hash
    currentHash ^= ZPC[moving.color][moving.type][m.fromX][m.fromY];
    if (u.capturedAtTo.type != NONE)
        currentHash ^= ZPC[u.capturedAtTo.color][u.capturedAtTo.type][m.toX][m.toY];
    currentHash ^= ZCAST[castling];
    if (epFile >= 0) currentHash ^= ZEP[epFile];

    // En-passant capture
    if (moving.type==PAWN && m.fromY!=m.toY && !board[m.toX][m.toY].type) {
        u.wasEp = true;
        u.epCaptureRow = m.fromX;
        PieceCell& ep = board[m.fromX][m.toY];
        currentHash ^= ZPC[ep.color][ep.type][m.fromX][m.toY];
        ep = {};
    }

    // Castling: move the rook
    if (moving.type==KING && (m.toY-m.fromY)*(m.toY-m.fromY)==4) {
        u.wasCastle   = true;
        u.rookFromCol = (m.toY > m.fromY) ? 7 : 0;
        u.rookToCol   = (m.toY > m.fromY) ? 5 : 3;
        PieceCell rook = board[m.fromX][u.rookFromCol];
        currentHash ^= ZPC[rook.color][rook.type][m.fromX][u.rookFromCol];
        currentHash ^= ZPC[rook.color][rook.type][m.fromX][u.rookToCol];
        board[m.fromX][u.rookToCol]   = rook;
        board[m.fromX][u.rookFromCol] = {};
    }

    // Place piece at destination
    board[m.toX][m.toY]   = moving;
    board[m.fromX][m.fromY] = {};

    // Promotion
    if (moving.type==PAWN && (m.toX==0 || m.toX==7)) {
        u.wasPromotion = true;
        PType pp = QUEEN;
        if      (m.promo=='R') pp = ROOK;
        else if (m.promo=='B') pp = BISHOP;
        else if (m.promo=='N') pp = KNIGHT;
        currentHash ^= ZPC[moving.color][PAWN][m.toX][m.toY];
        board[m.toX][m.toY].type = pp;
        currentHash ^= ZPC[moving.color][pp][m.toX][m.toY];
    } else {
        currentHash ^= ZPC[moving.color][moving.type][m.toX][m.toY];
    }

    // Update castling rights
    if (moving.type==KING) castling &= (moving.color==BALTA) ? ~3u : ~12u;
    if (moving.type==ROOK) {
        if      (m.fromX==7 && m.fromY==7) castling &= ~1u;
        else if (m.fromX==7 && m.fromY==0) castling &= ~2u;
        else if (m.fromX==0 && m.fromY==7) castling &= ~4u;
        else if (m.fromX==0 && m.fromY==0) castling &= ~8u;
    }
    if (u.capturedAtTo.type==ROOK) {
        if      (m.toX==7 && m.toY==7) castling &= ~1u;
        else if (m.toX==7 && m.toY==0) castling &= ~2u;
        else if (m.toX==0 && m.toY==7) castling &= ~4u;
        else if (m.toX==0 && m.toY==0) castling &= ~8u;
    }

    // New EP square
    epFile = -1; epRank = -1;
    if (moving.type==PAWN) {
        int dist = m.toX - m.fromX;
        if (dist*dist == 4) { // two-square push
            epFile = m.fromY;
            epRank = (m.fromX + m.toX) / 2;
        }
    }

    // Half-move clock
    if (moving.type==PAWN || u.capturedAtTo.type!=NONE) halfMoveClock = 0;
    else ++halfMoveClock;

    currentHash ^= ZCAST[castling];
    if (epFile >= 0) currentHash ^= ZEP[epFile];
    currentHash ^= ZSIDE;

    currentTurn = opp(currentTurn);
    hashHistory.push_back(currentHash);
    return u;
}

// ── unmakeMove ─────────────────────────────────────────────────────────────
void SachmatuLenta::unmakeMove(const Move& m, const UndoInfo& u) {
    hashHistory.pop_back();
    currentTurn = opp(currentTurn);

    PieceCell moved = board[m.toX][m.toY];
    if (u.wasPromotion) moved.type = PAWN;

    board[m.fromX][m.fromY] = moved;
    board[m.toX][m.toY]     = u.capturedAtTo;

    if (u.wasEp) {
        board[u.epCaptureRow][m.toY] = { PAWN, opp(currentTurn) };
        board[m.toX][m.toY]          = {};
    }

    if (u.wasCastle) {
        PieceCell rook = board[m.fromX][u.rookToCol];
        board[m.fromX][u.rookFromCol] = rook;
        board[m.fromX][u.rookToCol]   = {};
    }

    castling      = u.castlingBefore;
    epFile        = u.epFileBefore;
    epRank        = u.epRankBefore;
    halfMoveClock = u.halfMoveClockBefore;
    currentHash   = u.hashBefore;
}

// ── leavesKingInCheck ──────────────────────────────────────────────────────
bool SachmatuLenta::leavesKingInCheck(const Move& m, Spalva s) {
    UndoInfo u = makeMove(m);
    bool inCheck = isInCheck(s);
    unmakeMove(m, u);
    return inCheck;
}

// ── generateLegalMoves ─────────────────────────────────────────────────────
std::vector<SachmatuLenta::Move> SachmatuLenta::generateLegalMoves(Spalva s) {
    std::vector<Move> pseudo;
    pseudo.reserve(64);
    generatePseudoLegal(s, pseudo);
    std::vector<Move> legal;
    legal.reserve(pseudo.size());
    for (const auto& m : pseudo)
        if (!leavesKingInCheck(m, s)) legal.push_back(m);
    return legal;
}

// ── syncMove ───────────────────────────────────────────────────────────────
bool SachmatuLenta::syncMove(int fr, int fc, int tr, int tc) {
    std::vector<Move> pseudo;
    generatePseudoLegal(currentTurn, pseudo);
    for (const auto& m : pseudo) {
        if (m.fromX==fr && m.fromY==fc && m.toX==tr && m.toY==tc) {
            if (!leavesKingInCheck(m, currentTurn)) {
                makeMove(m);
                return true;
            }
        }
    }
    return false;
}

// ── PeSTO piece-square tables ──────────────────────────────────────────────
// Row 0 = rank 8 (black's back rank / white's "far" rank).
// White uses pst[row][col] directly; black flips: pst[7-row][col].

static const int MG_PAWN[8][8] = {
    {  0,  0,  0,  0,  0,  0,  0,  0},
    { 98,134, 61, 95, 68,126, 34,-11},
    { -6,  7, 26, 31, 65, 56, 25,-20},
    {-14, 13,  6, 21, 23, 12, 17,-23},
    {-27, -2, -5, 12, 17,  6, 10,-25},
    {-26, -4, -4,-10,  3,  3, 33,-12},
    {-35, -1,-20,-23,-15, 24, 38,-22},
    {  0,  0,  0,  0,  0,  0,  0,  0},
};
static const int EG_PAWN[8][8] = {
    {  0,  0,  0,  0,  0,  0,  0,  0},
    {178,173,158,134,147,132,165,187},
    { 94,100, 85, 67, 56, 53, 82, 84},
    { 32, 24, 13,  5, -2,  4, 17, 17},
    { 13,  9, -3, -7, -7, -8,  3, -1},
    {  4,  7, -6,  1,  0, -5, -1, -8},
    { 13,  8,  8, 10, 13,  0,  2, -7},
    {  0,  0,  0,  0,  0,  0,  0,  0},
};
static const int MG_KNIGHT[8][8] = {
    {-167,-89,-34,-49, 61,-97,-15,-107},
    { -73,-41, 72, 36, 23, 62,  7, -17},
    { -47, 60, 37, 65, 84,129, 73,  44},
    {  -9, 17, 19, 53, 37, 69, 18,  22},
    { -13,  4, 16, 13, 28, 19, 21,  -8},
    { -23, -9, 12, 10, 19, 17, 25, -16},
    { -29,-53,-12, -3, -1, 18,-14, -19},
    {-105,-21,-58,-33,-17,-28,-19, -23},
};
static const int EG_KNIGHT[8][8] = {
    {-58,-38,-13,-28,-31,-27,-63,-99},
    {-25, -8,-25, -2, -9,-25,-24,-52},
    {-24,-20, 10,  9, -1, -9,-19,-41},
    {-17,  3, 22, 22, 22, 11,  8,-18},
    {-18, -6, 16, 25, 16, 17,  4,-18},
    {-23, -3, -1, 15, 10, -3,-20,-22},
    {-42,-20,-10, -5, -2,-20,-23,-44},
    {-29,-51,-23,-15,-22,-18,-50,-64},
};
static const int MG_BISHOP[8][8] = {
    {-29,  4,-82,-37,-25,-42,  7, -8},
    {-26, 16,-18,-13, 30, 59, 18,-47},
    {-16, 37, 43, 40, 35, 50, 37, -2},
    { -4,  5, 19, 50, 37, 37,  7, -2},
    { -6, 13, 13, 26, 34, 12, 10,  4},
    {  0, 15, 15, 15, 14, 27, 18, 10},
    {  4, 15, 16,  0,  7, 21, 33,  1},
    {-33, -3,-14,-21,-13,-12,-39,-21},
};
static const int EG_BISHOP[8][8] = {
    {-14,-21,-11, -8, -7, -9,-17,-24},
    { -8, -4,  7,-12, -3,-13, -4,-14},
    {  2, -8,  0, -1, -2,  6,  0,  4},
    { -3,  9, 12,  9, 14, 10,  3,  2},
    { -6,  3, 13, 19,  7, 10, -3, -9},
    {-12, -3,  8, 10, 13,  3, -7,-15},
    {-14,-18, -7, -1,  4, -9,-15,-27},
    {-23, -9,-23, -5, -9,-16, -5,-17},
};
static const int MG_ROOK[8][8] = {
    { 32, 42, 32, 51, 63,  9, 31, 43},
    { 27, 32, 58, 62, 80, 67, 26, 44},
    { -5, 19, 26, 36, 17, 45, 61, 16},
    {-24,-11,  7, 26, 24, 35,-18,-22},
    {-36,-26,-12, -1,  9, -7,  6,-23},
    {-45,-25,-16,-17,  3,  0, -5,-33},
    {-44,-16,-20, -9, -1, 11, -6,-71},
    {-19,-13,  1, 17, 16,  7,-37,-26},
};
static const int EG_ROOK[8][8] = {
    { 13, 10, 18, 15, 12, 12,  8,  5},
    { 11, 13, 13, 11, -3,  3,  8,  3},
    {  7,  7,  7,  5,  4, -3, -5, -3},
    {  4,  3, 13,  1,  2,  1, -1,  2},
    {  3,  5,  8,  4, -5, -6, -8,-11},
    { -4,  0, -5, -1, -7,-12, -8,-16},
    { -6, -6,  0,  2, -9, -9,-11,-16},
    { -9,  2,  3, -1, -5,-13,  4,-20},
};
static const int MG_QUEEN[8][8] = {
    {-28,  0, 29, 12, 59, 44, 43, 45},
    {-24,-39, -5,  1,-16, 57, 28, 54},
    {-13,-17,  7,  8, 29, 56, 47, 57},
    {-27,-27,-16,-16, -1, 17, -2,  1},
    { -9,-26, -9,-10, -2, -4,  3, -3},
    {-14,  2,-11, -2, -5,  2, 14,  5},
    {-35, -8, 11,  2,  8, 15, -3,  1},
    { -1,-18, -9, 10,-15,-25,-31,-50},
};
static const int EG_QUEEN[8][8] = {
    { -9, 22, 22, 27, 27, 19, 10, 20},
    {-17, 20, 32, 41, 58, 25, 30,  0},
    {-20,  6,  9, 49, 47, 35, 19,  9},
    {  3, 22, 24, 45, 57, 40, 57, 36},
    {-18, 28, 19, 47, 31, 34, 39, 23},
    {-16,-27, 15,  6,  9, 17, 10,  5},
    {-22,-23,-30,-16,-16,-23,-36,-32},
    {-33,-28,-22,-43, -5,-32,-20,-41},
};
static const int MG_KING[8][8] = {
    {-65, 23, 16,-15,-56,-34,  2, 13},
    { 29, -1,-20, -7, -8, -4,-38,-29},
    { -9, 24,  2,-16,-20,  6, 22,-22},
    {-17,-20,-12,-27,-30,-25,-14,-36},
    {-49, -1,-27,-39,-46,-44,-33,-51},
    {-14,-14,-22,-46,-44,-30,-15,-27},
    {  1,  7, -8,-64,-43,-16,  9,  8},
    {-15, 36, 12,-54,  8,-28, 24, 14},
};
static const int EG_KING[8][8] = {
    {-74,-35,-18,-18,-11, 15,  4,-17},
    {-12, 17, 14, 17, 17, 38, 23, 11},
    { 10, 17, 23, 15, 20, 45, 44, 13},
    { -8, 22, 24, 27, 26, 33, 26,  3},
    {-18, -4, 21, 24, 27, 23,  9,-11},
    {-19, -3, 11, 21, 23, 16,  7, -9},
    {-27,-11,  4, 13, 14,  4, -5,-17},
    {-53,-34,-21,-11,-28,-14,-24,-43},
};

static const int MG_VAL[7] = { 0,  82, 337, 365, 477, 1025, 0 };
static const int EG_VAL[7] = { 0,  94, 281, 297, 512,  936, 0 };
static const int PHASE_W[7] = { 0,   0,   1,   1,   2,    4, 0 };

int SachmatuLenta::pstScore(PType pt, Spalva c, int r, int col, bool mg) const {
    int row = (c == BALTA) ? r : (7 - r);
    switch (pt) {
        case PAWN:   return mg ? MG_PAWN  [row][col] : EG_PAWN  [row][col];
        case KNIGHT: return mg ? MG_KNIGHT[row][col] : EG_KNIGHT[row][col];
        case BISHOP: return mg ? MG_BISHOP[row][col] : EG_BISHOP[row][col];
        case ROOK:   return mg ? MG_ROOK  [row][col] : EG_ROOK  [row][col];
        case QUEEN:  return mg ? MG_QUEEN [row][col] : EG_QUEEN [row][col];
        case KING:   return mg ? MG_KING  [row][col] : EG_KING  [row][col];
        default:     return 0;
    }
}

// evalTapered: returns score from white's perspective (positive = white better)
int SachmatuLenta::evalTapered() const {
    int mg = 0, eg = 0, phase = 0;
    for (int r = 0; r < 8; r++) {
        for (int c = 0; c < 8; c++) {
            PType pt = board[r][c].type;
            if (pt == NONE) continue;
            Spalva col  = board[r][c].color;
            int sign    = (col == BALTA) ? 1 : -1;
            phase      += PHASE_W[pt];
            mg         += sign * (MG_VAL[pt] + pstScore(pt, col, r, c, true));
            eg         += sign * (EG_VAL[pt] + pstScore(pt, col, r, c, false));
        }
    }
    if (phase > 24) phase = 24;
    return (mg * phase + eg * (24 - phase)) / 24;
}

int SachmatuLenta::evaluateBoard() const { return evalTapered(); }

// ── Move ordering ──────────────────────────────────────────────────────────
// MVV-LVA: attacker rows, victim columns (NONE=0 … KING=6)
static const int MVV_LVA[7][7] = {
    {0,    0,    0,    0,    0,    0,    0},
    {0, 1050, 2050, 3050, 4050, 5050, 6050},
    {0, 1040, 2040, 3040, 4040, 5040, 6040},
    {0, 1030, 2030, 3030, 4030, 5030, 6030},
    {0, 1020, 2020, 3020, 4020, 5020, 6020},
    {0, 1010, 2010, 3010, 4010, 5010, 6010},
    {0, 1000, 2000, 3000, 4000, 5000, 6000},
};

void SachmatuLenta::scoreMoves(std::vector<Move>& mv, int ply, const Move& ttBest) const {
    for (auto& m : mv) {
        if (m == ttBest) { m.score = 2'000'000; continue; }
        PType victim   = board[m.toX][m.toY].type;
        PType attacker = board[m.fromX][m.fromY].type;
        bool  isEP     = (attacker==PAWN && m.fromY!=m.toY && victim==NONE);
        if (victim != NONE) {
            m.score = 1'000'000 + MVV_LVA[attacker][victim];
        } else if (isEP) {
            m.score = 1'000'000 + MVV_LVA[PAWN][PAWN];
        } else {
            if (ply < MAX_PLY && killers[ply][0] == m) m.score = 900'000;
            else if (ply < MAX_PLY && killers[ply][1] == m) m.score = 800'000;
            else m.score = history[m.fromX][m.fromY][m.toX][m.toY];
        }
    }
}

// ── Quiescence search ──────────────────────────────────────────────────────
static constexpr int INF          = 1'000'000;
static constexpr int MATE_SCORE   =   900'000;
static constexpr int DELTA_MARGIN =       200;
static constexpr int MATE_BOUND   = MATE_SCORE - 64;   // 64 == MAX_PLY

// Normalise mate scores before storing in TT so they are ply-independent.
static inline int ttToStore(int score, int ply) {
    if (score >=  MATE_BOUND) return score + ply;
    if (score <= -MATE_BOUND) return score - ply;
    return score;
}
// Reverse normalisation on retrieval.
static inline int ttFromStore(int score, int ply) {
    if (score >=  MATE_BOUND) return score - ply;
    if (score <= -MATE_BOUND) return score + ply;
    return score;
}

int SachmatuLenta::qsearch(int alpha, int beta, int ply) {
    int standPat = (currentTurn == BALTA) ? evalTapered() : -evalTapered();
    if (standPat >= beta) return standPat;
    if (standPat > alpha) alpha = standPat;

    std::vector<Move> pseudo;
    pseudo.reserve(32);
    generatePseudoLegal(currentTurn, pseudo);

    Move dummy;
    scoreMoves(pseudo, ply, dummy);
    std::sort(pseudo.begin(), pseudo.end(), [](const Move& a, const Move& b){ return a.score > b.score; });

    for (const auto& m : pseudo) {
        PType victim  = board[m.toX][m.toY].type;
        bool  isEP    = (board[m.fromX][m.fromY].type==PAWN && m.fromY!=m.toY && victim==NONE);
        bool  isQProm = (board[m.fromX][m.fromY].type==PAWN && m.promo=='Q' && victim==NONE);
        if (victim==NONE && !isEP && !isQProm) continue;   // skip quiet non-promotions

        // Delta pruning — promotion gain = queen minus pawn
        int captVal = (victim != NONE) ? EG_VAL[victim] :
                      isEP             ? EG_VAL[PAWN]   :
                                         EG_VAL[QUEEN] - EG_VAL[PAWN];
        if (standPat + captVal + DELTA_MARGIN <= alpha) continue;

        if (leavesKingInCheck(m, currentTurn)) continue;
        UndoInfo u = makeMove(m);
        int score  = -qsearch(-beta, -alpha, ply+1);
        unmakeMove(m, u);

        if (score >= beta) return score;
        if (score > alpha) alpha = score;
    }
    return alpha;
}

// ── Main search ────────────────────────────────────────────────────────────
int SachmatuLenta::search(int depth, int alpha, int beta, int ply, bool nullOk) {
    if (timeUp()) return 0;

    // Draw detection
    if (ply > 0) {
        if (halfMoveClock >= 100) return 0;
        int reps = 0;
        for (uint64_t h : hashHistory) if (h == currentHash) ++reps;
        if (reps >= 2) return 0;
    }

    // TT probe
    const uint64_t idx = currentHash & (TT_SIZE - 1);
    TTEntry& tte = ttTable[idx];
    Move ttBest;
    if (tte.hash == currentHash && tte.depth >= (int8_t)depth) {
        int ttScore = ttFromStore(tte.score, ply);
        if      (tte.bound == TT_EXACT)                   return ttScore;
        else if (tte.bound == TT_LOWER && ttScore >= beta) return ttScore;
        else if (tte.bound == TT_UPPER && ttScore <= alpha) return ttScore;
        ttBest = tte.best;
    } else if (tte.hash == currentHash) {
        ttBest = tte.best;
    }

    if (depth == 0) return qsearch(alpha, beta, ply);

    bool inCheck = isInCheck(currentTurn);

    // Null-move pruning
    if (nullOk && !inCheck && depth >= 3) {
        // Save EP state; flip turn
        int savedEpFile = epFile, savedEpRank = epRank;
        currentHash ^= ZSIDE;
        if (epFile >= 0) currentHash ^= ZEP[epFile];
        epFile = -1; epRank = -1;
        currentTurn = opp(currentTurn);
        hashHistory.push_back(currentHash);

        int R = (depth >= 6) ? 3 : 2;
        int nullScore = -search(depth-1-R, -beta, -beta+1, ply+1, false);

        hashHistory.pop_back();
        currentTurn = opp(currentTurn);
        epFile = savedEpFile; epRank = savedEpRank;
        currentHash ^= ZSIDE;
        if (epFile >= 0) currentHash ^= ZEP[epFile];

        if (nullScore >= beta) return nullScore;
    }

    // Generate, score, sort
    std::vector<Move> moves;
    moves.reserve(64);
    generatePseudoLegal(currentTurn, moves);
    scoreMoves(moves, ply, ttBest);
    std::sort(moves.begin(), moves.end(), [](const Move& a, const Move& b){ return a.score > b.score; });

    int     legalCount = 0;
    int     bestScore  = -INF;
    Move    bestMove;
    TTBound bound      = TT_UPPER;

    for (int i = 0; i < (int)moves.size(); i++) {
        const Move& m = moves[i];
        if (leavesKingInCheck(m, currentTurn)) continue;
        legalCount++;

        bool isCapture = (board[m.toX][m.toY].type != NONE) ||
                         (board[m.fromX][m.fromY].type == PAWN &&
                          m.fromY != m.toY && board[m.toX][m.toY].type == NONE);
        bool isPromo   = (m.promo != 0);

        UndoInfo u = makeMove(m);
        int score;

        if (legalCount == 1) {
            score = -search(depth-1, -beta, -alpha, ply+1, true);
        } else {
            // LMR: reduce late, quiet moves
            int red = 0;
            if (depth >= 3 && !isCapture && !isPromo && !inCheck) {
                red = (legalCount > 8) ? 2 : (legalCount > 4) ? 1 : 0;
            }
            score = -search(depth-1-red, -alpha-1, -alpha, ply+1, true);
            if (score > alpha && red > 0)
                score = -search(depth-1, -alpha-1, -alpha, ply+1, true);
            if (score > alpha && score < beta)
                score = -search(depth-1, -beta, -alpha, ply+1, true);
        }
        unmakeMove(m, u);

        if (timeUp()) return 0;

        if (score > bestScore) { bestScore = score; bestMove = m; }
        if (score > alpha)     { alpha = score; bound = TT_EXACT; }
        if (score >= beta) {
            if (!isCapture && ply < MAX_PLY) {
                if (!(killers[ply][0] == m)) {
                    killers[ply][1] = killers[ply][0];
                    killers[ply][0] = m;
                }
                history[m.fromX][m.fromY][m.toX][m.toY] += depth * depth;
            }
            bound = TT_LOWER;
            break;
        }
    }

    if (legalCount == 0)
        return inCheck ? -(MATE_SCORE - ply) : 0;

    if (!timeUp() && bestMove.isValid()) {
        tte.hash  = currentHash;
        tte.score = ttToStore(bestScore, ply);
        tte.depth = (int8_t)std::min(depth, 127);
        tte.bound = bound;
        tte.best  = bestMove;
    }
    return bestScore;
}

// ── getBestMove: iterative deepening ──────────────────────────────────────
// strengthLevel [0,100]: 0 = shallowest/fastest, 100 = full depth/time.
SachmatuLenta::Move SachmatuLenta::getBestMove(Spalva s, int strengthLevel) {
    const int level   = strengthLevel < 0 ? 0 : (strengthLevel > 100 ? 100 : strengthLevel);
    const int maxDepth = 1 + level * 5 / 100;       // [1, 6]
    timeLimitMs        = 300 + level * 22;            // [300ms, 2500ms]

    currentTurn = s;
    memset(killers, 0, sizeof(killers));
    memset(history, 0, sizeof(history));
    // Preserve hashHistory: positions from syncMove() calls are needed so the
    // repetition detector inside search() can see prior game positions.
    currentHash = computeHash();

    searchStart = std::chrono::steady_clock::now();

    // Pre-filter legal root moves once
    std::vector<Move> rootMoves;
    {
        std::vector<Move> pseudo;
        pseudo.reserve(64);
        generatePseudoLegal(s, pseudo);
        for (const auto& m : pseudo)
            if (!leavesKingInCheck(m, s)) rootMoves.push_back(m);
    }
    if (rootMoves.empty()) return Move();

    Move best = rootMoves[0];

    for (int depth = 1; depth <= maxDepth && !timeUp(); depth++) {
        // Order root moves using previous-iteration TT best
        Move ttBest;
        {
            uint64_t idx = currentHash & (TT_SIZE - 1);
            if (ttTable[idx].hash == currentHash) ttBest = ttTable[idx].best;
        }
        scoreMoves(rootMoves, 0, ttBest);
        std::sort(rootMoves.begin(), rootMoves.end(),
                  [](const Move& a, const Move& b){ return a.score > b.score; });

        int alpha = -INF, iterBestScore = -INF;
        Move iterBest;

        for (const auto& m : rootMoves) {
            UndoInfo u = makeMove(m);
            int score  = -search(depth-1, -INF, -alpha, 1, true);
            unmakeMove(m, u);
            if (timeUp()) break;
            if (score > iterBestScore) { iterBestScore = score; iterBest = m; }
            if (score > alpha) alpha = score;
        }
        if (iterBest.isValid() && !timeUp()) best = iterBest;
    }
    return best;
}

// ── minimax: backward-compatible shim ─────────────────────────────────────
int SachmatuLenta::minimax(int depth, bool isMax, int alpha, int beta) {
    if (depth == 0) return evalTapered();   // from white's perspective
    Spalva side = isMax ? BALTA : JUODA;
    std::vector<Move> moves;
    generatePseudoLegal(side, moves);
    bool anyLegal = false;
    int best = isMax ? -INF : INF;
    for (const auto& m : moves) {
        if (leavesKingInCheck(m, side)) continue;
        anyLegal = true;
        UndoInfo u = makeMove(m);
        int val = minimax(depth-1, !isMax, alpha, beta);
        unmakeMove(m, u);
        if (isMax) { best = std::max(best, val); alpha = std::max(alpha, best); }
        else       { best = std::min(best, val); beta  = std::min(beta,  best); }
        if (beta <= alpha) break;
    }
    if (!anyLegal) return isInCheck(side) ? (isMax ? -MATE_SCORE : MATE_SCORE) : 0;
    return best;
}

// ── perft ──────────────────────────────────────────────────────────────────
uint64_t SachmatuLenta::perft(int depth) {
    if (depth == 0) return 1;
    std::vector<Move> moves;
    moves.reserve(64);
    generatePseudoLegal(currentTurn, moves);
    uint64_t nodes = 0;
    for (const auto& m : moves) {
        if (leavesKingInCheck(m, currentTurn)) continue;
        UndoInfo u = makeMove(m);
        nodes += perft(depth - 1);
        unmakeMove(m, u);
    }
    return nodes;
}
