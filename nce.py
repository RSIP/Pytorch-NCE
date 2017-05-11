# the NCE module written for pytorch

import torch
import torch.nn as nn
from torch.autograd import Variable
import torch.nn.functional as F


def take(iterator, count):
    """Take the next `count` items of iterator

    Return:
        a list of the items
    """
    return list(next(iterator) for _ in range(count))


class NCELoss(nn.Module):
    """Noise Contrastive Estimation
    NCE is to eliminate the computational cost of softmax
    normalization.
    Ref:
        X.Chen etal Recurrent neural network language
        model training with noise contrastive estimation
        for speech recognition
        https://core.ac.uk/download/pdf/42338485.pdf

    Attributes:
        emb_size: embedding size
        ntokens: vocabulary size
        noise: the distribution of noise
        noise_ratio: $\frac{#noises}{#real data samples}$ (k in paper)
        norm_term: the normalization term (lnZ in paper)
        size_average: average the loss by batch size
        decoder: the decoder matrix

    Shape:
        - noise: :math:`(V)` where `V = vocabulary size`
        - decoder: :math:`(E, V)` where `E = embedding size`
    """

    def __init__(self,
                 ntokens,
                 emb_size,
                 noise,
                 noise_ratio=10,
                 norm_term=9,
                 size_average=True,
                 decoder_weight=None,
                 ):
        super(NCELoss, self).__init__()

        self.noise = noise
        self.noise_ratio = noise_ratio
        self.norm_term = norm_term
        self.ntokens = ntokens
        self.size_average = size_average
        self.decoder = IndexLinear(emb_size, ntokens)
        # Weight tying
        if decoder_weight:
            self.decoder.weight = decoder_weight

    @profile
    def forward(self, input, target):
        """compute the loss with output and the desired target

        Parameters:
            input: the output of decoder, before softmax.
            target: the supervised training label.

        Shape:
            - input: :math:`(N, E)` where `N = number of tokens, E = embedding size`
            - target: :math:`(N)`

        Return:
            the scalar NCELoss Variable ready for backward
        """

        assert input.size(0) == target.size(0)

        data_prob = Variable(torch.zeros(target.size())).cuda()
        noise_in_data_probs = Variable(torch.zeros(target.size(0), self.noise_ratio)).cuda()
        noise_probs = Variable(torch.zeros(noise_in_data_probs.size())).cuda()
        torch.cuda.synchronize()
        for idx, target_idx in enumerate(target):
            noise_samples = torch.multinomial(
                self.noise,
                self.noise_ratio,
                replacement=True)
            torch.cuda.synchronize()
            data_prob[idx], noise_in_data_probs[idx] = self._get_prob(input[idx], target_idx, noise_samples)
            torch.cuda.synchronize()
            noise_probs[idx] = self.noise[noise_samples]
            torch.cuda.synchronize()

        rnn_loss = torch.log(data_prob / (
            data_prob + Variable(self.noise_ratio * self.noise[target.data]
        )))

        noise_loss = torch.sum(
            torch.log((self.noise_ratio * noise_probs) / (noise_in_data_probs + self.noise_ratio * noise_probs)), 1
        )

        loss = -1 * torch.sum(rnn_loss + noise_loss)
        if self.size_average:
            loss = loss / target.size(0)

        return loss

    def _get_prob(self, embedding, target_idx, noise_idx):

        indices = torch.cat([target_idx.data, noise_idx])
        probs = self.decoder(embedding.unsqueeze(0), indices).view(-1)
        probs = probs.sub(self.norm_term).exp()
        return probs[0], probs[1:]


class IndexLinear(nn.Linear):
    """A linear layer that only decodes the results of provided indices

    Args:
        index: the indices of interests.

    Shape:
        - Input :math:`(N, in\_features)`
        - Index :math:`(M)` where `max(M) <= N`
    """

    def forward(self, input, indices):
        out = F.linear(input, self.weight[indices], bias=self.bias[indices])
        return out
