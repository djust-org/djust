//! Longest Increasing Subsequence (LIS) algorithm for VDOM keyed diffing.
//!
//! When diffing keyed children, elements whose old indices form an increasing
//! subsequence in new-order can stay in place — only the remaining elements
//! need `MoveChild` patches. Computing the LIS minimizes the number of moves.
//!
//! Uses the O(n log n) patience-sorting algorithm with predecessor tracking.

/// Compute the Longest Increasing Subsequence of `seq`.
///
/// Returns the indices (into `seq`) that form the LIS. When multiple LIS
/// of the same length exist, this returns one of them (not necessarily unique).
///
/// For example, given `[3, 1, 4, 1, 5, 9, 2, 6]`, one valid LIS has
/// length 4 (e.g., values `[1, 4, 5, 6]` at indices `[1, 2, 4, 7]`).
pub fn longest_increasing_subsequence(seq: &[usize]) -> Vec<usize> {
    if seq.is_empty() {
        return vec![];
    }

    let n = seq.len();
    // tails[i] = smallest tail value for an increasing subsequence of length i+1
    let mut tails: Vec<usize> = Vec::new();
    // indices_at[i] = index in `seq` of the element stored at tails[i]
    let mut indices_at: Vec<usize> = Vec::new();
    // predecessor[i] = index in `seq` of the predecessor of seq[i] in the LIS
    let mut predecessor: Vec<Option<usize>> = vec![None; n];

    for i in 0..n {
        let val = seq[i];
        // Binary search: find leftmost position in tails where tails[pos] >= val
        let pos = tails.partition_point(|&t| t < val);

        if pos == tails.len() {
            tails.push(val);
            indices_at.push(i);
        } else {
            tails[pos] = val;
            indices_at[pos] = i;
        }

        predecessor[i] = if pos > 0 {
            Some(indices_at[pos - 1])
        } else {
            None
        };
    }

    // Reconstruct the LIS by walking predecessors from the last element
    let lis_len = tails.len();
    let mut result = Vec::with_capacity(lis_len);
    let mut k = match indices_at.last() {
        Some(&idx) => idx,
        None => return vec![],
    };

    for _ in 0..lis_len {
        result.push(k);
        match predecessor[k] {
            Some(prev) => k = prev,
            None => break,
        }
    }
    result.reverse();
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_empty_sequence() {
        let empty: Vec<usize> = vec![];
        assert_eq!(longest_increasing_subsequence(&[]), empty);
    }

    #[test]
    fn test_single_element() {
        let result = longest_increasing_subsequence(&[42]);
        assert_eq!(result.len(), 1);
        assert_eq!(result[0], 0); // index 0
    }

    #[test]
    fn test_already_sorted() {
        let seq = vec![0, 1, 2, 3, 4, 5];
        let result = longest_increasing_subsequence(&seq);
        // LIS should be the entire sequence
        assert_eq!(result.len(), 6);
        // Values at these indices should be increasing
        for i in 1..result.len() {
            assert!(seq[result[i]] > seq[result[i - 1]]);
        }
    }

    #[test]
    fn test_reverse_sorted() {
        let seq = vec![5, 4, 3, 2, 1, 0];
        let result = longest_increasing_subsequence(&seq);
        // LIS of a strictly decreasing sequence is 1
        assert_eq!(result.len(), 1);
    }

    #[test]
    fn test_known_sequence() {
        // seq = [3, 1, 4, 1, 5, 9, 2, 6]
        // LIS length should be 4 (e.g., 1, 4, 5, 6 or 1, 4, 5, 9)
        let seq = vec![3, 1, 4, 1, 5, 9, 2, 6];
        let result = longest_increasing_subsequence(&seq);
        assert_eq!(result.len(), 4);
        // Verify the subsequence is actually increasing
        for i in 1..result.len() {
            assert!(seq[result[i]] > seq[result[i - 1]]);
        }
    }

    #[test]
    fn test_duplicates() {
        let seq = vec![2, 2, 2, 2];
        let result = longest_increasing_subsequence(&seq);
        // All equal — LIS is 1 (strictly increasing)
        assert_eq!(result.len(), 1);
    }

    #[test]
    fn test_two_elements_increasing() {
        let result = longest_increasing_subsequence(&[1, 3]);
        assert_eq!(result.len(), 2);
    }

    #[test]
    fn test_two_elements_decreasing() {
        let result = longest_increasing_subsequence(&[3, 1]);
        assert_eq!(result.len(), 1);
    }

    #[test]
    fn test_random_permutation() {
        // Permutation of 0..10: [7, 2, 8, 1, 3, 0, 6, 9, 5, 4]
        let seq = vec![7, 2, 8, 1, 3, 0, 6, 9, 5, 4];
        let result = longest_increasing_subsequence(&seq);
        // LIS length should be 4 (e.g., 2, 3, 6, 9)
        assert!(result.len() >= 4);
        // Verify increasing property
        for i in 1..result.len() {
            assert!(seq[result[i]] > seq[result[i - 1]]);
        }
    }

    #[test]
    fn test_single_move_at_end() {
        // [1, 2, 3, 4, 5, 0] — element 0 moved to end in new order
        // LIS should be n-1 (all but the moved element stay in order)
        let seq = vec![1, 2, 3, 4, 5, 0];
        let result = longest_increasing_subsequence(&seq);
        assert_eq!(result.len(), 5); // [1,2,3,4,5] is the LIS
    }

    #[test]
    fn test_large_sequence() {
        // 1000 elements in order — LIS should be 1000
        let seq: Vec<usize> = (0..1000).collect();
        let result = longest_increasing_subsequence(&seq);
        assert_eq!(result.len(), 1000);
    }
}
