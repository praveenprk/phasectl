// Naive C++ symbol extraction (M4, docs/internal/NATIVE_INDEXER_PLAN.md).
//
// Regex/line-based, not an AST: no preprocessor handling, no cross-file
// resolution, single-line signatures only (a declaration whose `(args)`
// or `{` spans multiple lines is missed). That's an explicit, accepted
// limitation for a first cut — see the plan's M4 entry. First target is
// the indexer's own C++ source (self-hosted dogfooding); other source
// languages come later (M7).
//
// Detects:
//   - class/struct declarations ending in `{` on the same line.
//   - function/method definitions: a `name(args) {` (optionally followed
//     by `const`/`noexcept` before the brace) whose head isn't a
//     control-flow keyword. `Qualifier::name(...)` is tagged as a method.
// Misses: multi-line signatures, operator overloads, templates spanning
// multiple lines, anything inside a string/char literal that happens to
// look like a declaration (literals aren't specially handled).

#pragma once

#include <cctype>
#include <regex>
#include <stack>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include "store.hpp"

namespace indexer {

namespace detail {

inline const std::unordered_set<std::string>& cpp_keywords() {
    static const std::unordered_set<std::string> kw = {
        "if",     "for",      "while",     "switch",  "catch",
        "return", "sizeof",   "new",       "delete",  "do",
        "else",   "using",    "namespace", "typedef", "template",
        "static_assert", "throw", "class", "struct",  "union",
        "enum",   "case",     "default",   "explicit","noexcept",
        "const",
    };
    return kw;
}

// Blanks out `//` and `/* */` comments, preserving newlines and overall
// character offsets so line numbers computed later stay accurate.
inline std::string strip_comments(const std::string& src) {
    std::string out;
    out.reserve(src.size());
    bool in_line_comment = false;
    bool in_block_comment = false;
    for (std::size_t i = 0; i < src.size(); ++i) {
        char c = src[i];
        char next = (i + 1 < src.size()) ? src[i + 1] : '\0';
        if (in_line_comment) {
            if (c == '\n') {
                in_line_comment = false;
                out.push_back(c);
            } else {
                out.push_back(' ');
            }
            continue;
        }
        if (in_block_comment) {
            if (c == '*' && next == '/') {
                in_block_comment = false;
                out.push_back(' ');
                out.push_back(' ');
                ++i;
            } else {
                out.push_back(c == '\n' ? '\n' : ' ');
            }
            continue;
        }
        if (c == '/' && next == '/') {
            in_line_comment = true;
            out.push_back(' ');
            out.push_back(' ');
            ++i;
            continue;
        }
        if (c == '/' && next == '*') {
            in_block_comment = true;
            out.push_back(' ');
            out.push_back(' ');
            ++i;
            continue;
        }
        out.push_back(c);
    }
    return out;
}

// Maps the line of every `{` to the line of its matching `}`.
inline std::unordered_map<int, int> match_braces(const std::string& text) {
    std::unordered_map<int, int> close_for_open_line;
    std::stack<int> open_lines;
    int line = 1;
    for (char c : text) {
        if (c == '{') {
            open_lines.push(line);
        } else if (c == '}') {
            if (!open_lines.empty()) {
                close_for_open_line[open_lines.top()] = line;
                open_lines.pop();
            }
        } else if (c == '\n') {
            ++line;
        }
    }
    return close_for_open_line;
}

inline std::string trim(const std::string& s) {
    std::size_t start = s.find_first_not_of(" \t\r");
    if (start == std::string::npos) return "";
    std::size_t end = s.find_last_not_of(" \t\r");
    return s.substr(start, end - start + 1);
}

// Scans backward from the end of `head` for a `[A-Za-z_:]\w*`-shaped
// token — the candidate function/method name (possibly qualified with
// `Class::`). Returns "" if the head doesn't end in such a token (e.g.
// operator overloads, whose last character before `(` isn't a word char).
inline std::string trailing_identifier(const std::string& head) {
    std::size_t end = head.size();
    while (end > 0 &&
           std::isspace(static_cast<unsigned char>(head[end - 1]))) {
        --end;
    }
    std::size_t start = end;
    auto is_ident_char = [](char c) {
        return std::isalnum(static_cast<unsigned char>(c)) || c == '_' ||
               c == ':';
    };
    while (start > 0 && is_ident_char(head[start - 1])) {
        --start;
    }
    if (start == end) return "";
    return head.substr(start, end - start);
}

}  // namespace detail

inline std::vector<Symbol> extract_cpp_symbols(const std::string& file_path,
                                                const std::string& source) {
    std::vector<Symbol> symbols;

    const std::string cleaned = detail::strip_comments(source);
    const auto close_for_open_line = detail::match_braces(cleaned);

    static const std::regex class_re(
        R"(^\s*(class|struct)\s+([A-Za-z_]\w*)\b[^{;]*\{\s*$)");
    static const std::regex func_re(
        R"(^\s*(.+?)\(([^()]*)\)\s*(?:const)?\s*(?:noexcept)?\s*\{\s*$)");

    std::size_t pos = 0;
    int line_no = 0;
    while (pos <= cleaned.size()) {
        std::size_t nl = cleaned.find('\n', pos);
        const std::string line = (nl == std::string::npos)
                                      ? cleaned.substr(pos)
                                      : cleaned.substr(pos, nl - pos);
        ++line_no;

        std::smatch m;
        if (std::regex_match(line, m, class_re)) {
            Symbol s;
            s.kind = m[1].str();
            s.name = m[2].str();
            s.qualified_name = s.name;
            s.file_path = file_path;
            s.start_line = line_no;
            auto it = close_for_open_line.find(line_no);
            s.end_line = it != close_for_open_line.end() ? it->second : line_no;
            s.signature = detail::trim(line);
            symbols.push_back(std::move(s));
        } else if (std::regex_match(line, m, func_re)) {
            const std::string head = m[1].str();
            const std::string args = m[2].str();
            const std::string token = detail::trailing_identifier(head);
            if (!token.empty()) {
                std::string qualifier;
                std::string name = token;
                std::size_t sep = token.rfind("::");
                if (sep != std::string::npos) {
                    qualifier = token.substr(0, sep);
                    name = token.substr(sep + 2);
                }
                if (!name.empty() && !detail::cpp_keywords().count(name) &&
                    !detail::cpp_keywords().count(qualifier)) {
                    Symbol s;
                    s.kind = qualifier.empty() ? "function" : "method";
                    s.name = name;
                    s.qualified_name = qualifier.empty()
                                            ? name
                                            : qualifier + "::" + name;
                    s.file_path = file_path;
                    s.start_line = line_no;
                    auto it = close_for_open_line.find(line_no);
                    s.end_line =
                        it != close_for_open_line.end() ? it->second : line_no;
                    s.signature = detail::trim(head) + "(" + detail::trim(args) + ")";
                    symbols.push_back(std::move(s));
                }
            }
        }

        if (nl == std::string::npos) break;
        pos = nl + 1;
    }

    return symbols;
}

}  // namespace indexer
