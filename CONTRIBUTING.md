# Contributing to wLib

Thank you for your interest in contributing to wLib! This guide will help you get started.

## 🚀 Getting Started

1. **Fork** the repository on GitHub
2. **Clone** your fork locally:
   ```bash
   git clone https://github.com/YOUR_USERNAME/wLib.git
   cd wLib
   ```
3. **Create a branch** for your change:
   ```bash
   git checkout -b feat/my-awesome-feature
   ```
4. **Set up the dev environment** (see [Development](README.md#️-development) in the README)

## 📝 Making Changes

### Code Style

- **Python**: Follow PEP 8 conventions. Use descriptive variable names and add docstrings to public functions.
- **JavaScript / Vue**: Use consistent formatting with the existing codebase. Single quotes, 2-space indentation.
- **CSS**: Use CSS custom properties for any colors or theme-related values.

### Commit Messages

We use [Conventional Commits](https://www.conventionalcommits.org/). Prefix your commit messages:

| Prefix | Use for |
|--------|---------|
| `feat:` | New features |
| `fix:` | Bug fixes |
| `docs:` | Documentation changes |
| `style:` | Code formatting (no logic change) |
| `refactor:` | Code restructuring |
| `chore:` | Build scripts, CI, dependencies |

**Examples:**
```
feat: add bulk game import from CSV
fix: wine prefix not applied for Proton launches
docs: update installation instructions for Fedora
```

## 🔀 Submitting a Pull Request

1. **Push** your branch to your fork:
   ```bash
   git push origin feat/my-awesome-feature
   ```
2. Open a **Pull Request** against `main` on the upstream repo
3. Fill in the PR template with a clear description of your changes
4. Link any related issues (e.g., "Closes #42")

### PR Checklist

- [ ] Code follows the existing style conventions
- [ ] Changes have been tested locally
- [ ] The app launches and runs without errors
- [ ] Frontend changes work in both dark and light themes
- [ ] New features include relevant documentation updates

## 🐛 Reporting Issues

### Bug Reports

When reporting a bug, please include:

- Steps to reproduce the issue
- Expected vs. actual behavior
- Your Linux distribution and version
- Wine/Proton version (if applicable)
- Any error output from the terminal or debug logs

### Feature Requests

Feature requests are welcome! Please describe:

- What problem the feature would solve
- Your proposed solution or approach
- Any alternatives you've considered

## 💡 Ideas for Contributions

If you're looking for ways to help, here are some areas:

- **Testing** — Try wLib on different distros and report compatibility issues
- **Documentation** — Improve guides, add screenshots, or translate docs
- **UI/UX** — Design improvements, accessibility enhancements, animations
- **Browser Extension** — Support for Firefox or additional site integrations
- **Game Engine Detection** — Better auto-detection of game engines for optimized launching

## ❓ Questions?

If you have questions about the codebase or need guidance, feel free to [open a discussion](https://github.com/kirin-3/wLib/discussions) or reach out through an issue.
