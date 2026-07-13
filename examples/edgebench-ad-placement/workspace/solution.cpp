#include <algorithm>
#include <iostream>

int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);

    int n = 0;
    if (!(std::cin >> n)) {
        return 1;
    }
    for (int i = 0; i < n; ++i) {
        int x = 0;
        int y = 0;
        long long target_area = 0;
        std::cin >> x >> y >> target_area;
        std::cout << x << ' ' << y << ' ' << std::min(x + 1, 10000) << ' '
                  << std::min(y + 1, 10000) << '\n';
    }
    return 0;
}
